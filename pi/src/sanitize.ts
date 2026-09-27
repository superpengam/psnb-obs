/**
 * 出站指纹清洗 —— engine/psnb/sanitize.py 的 TS 移植。
 *
 * 上游内容审核按逐字精确匹配拦截，不是语义审核 → 一字改动即绕过。
 * 流水线：feature_precheck → rewrites → strip_regex → bare_key_shrink → trim
 */

import { readDataJson } from "./data.js";

export interface RawRule {
	from: string;
	to: string;
	note?: string;
}

export interface RawRegex {
	pattern: string;
	replacement: string;
	note?: string;
}

export interface RawProfile {
	label: string;
	features: string[];
	rewrites: RawRule[];
	strip_regex: RawRegex[];
	bare_key_regex: RawRegex[];
}

export interface RulesFile {
	profiles: Record<string, RawProfile>;
	identity_banners: Record<string, string>;
	neutral_rewrite: { text: string };
}

/**
 * Go 风格正则 → JS 正则。
 * Go 支持行内 `(?i)` 前缀，JS 不支持 —— 提取出来转成 flags，并剥掉该前缀。
 */
export function compileGoRegex(pattern: string, flags = "g"): RegExp {
	let p = pattern;
	let f = flags;
	const m = /^\(\?([ims]+)\)/.exec(p);
	if (m) {
		const inline = m[1] ?? "";
		for (const c of inline) if (!f.includes(c)) f += c;
		p = p.slice(m[0].length);
	}
	return new RegExp(p, f);
}

export class Profile {	readonly name: string;
	readonly label: string;
	readonly features: string[];
	private readonly rewrites: RawRule[];
	private readonly stripRes: Array<[RegExp, string]>;
	readonly bareRes: Array<[RegExp, string]>;

	constructor(name: string, raw: RawProfile) {
		this.name = name;
		this.label = raw.label;
		this.features = raw.features ?? [];
		this.rewrites = raw.rewrites ?? [];
		this.stripRes = (raw.strip_regex ?? []).map((r) => [compileGoRegex(r.pattern), r.replacement ?? ""]);
		this.bareRes = (raw.bare_key_regex ?? []).map((r) => [compileGoRegex(r.pattern), r.replacement ?? ""]);
	}

	/**
	 * 特征预检：Contains 快路径 + 不要求冒号的裸键名正则兜底。
	 * 两个坑：features 大小写敏感会漏 X-Anthropic-...；strip 正则要求冒号会漏裸键名。
	 */
	hasFingerprint(text: string): boolean {
		for (const f of this.features) if (text.includes(f)) return true;
		return this.bareRes.some(([re]) => re.test(text));
	}

	sanitizeText(text: string): string {
		if (!text || !this.hasFingerprint(text)) return text;
		let out = text;

		for (const { from, to } of this.rewrites) out = out.split(from).join(to);

		for (const [re, rep] of this.stripRes) {
			re.lastIndex = 0;
			if (re.test(out)) {
				re.lastIndex = 0;
				out = out.replace(re, rep);
			}
		}

		// 尾随裸 kv（cc_version=...; cc_entrypoint=...;）循环清到不动点
		if (out.includes("cc_")) {
			let prev = "";
			let guard = 0;
			while (prev !== out && guard++ < 8) {
				prev = out;
				for (const [re, rep] of this.stripRes) {
					if (re.source.includes("cc_")) {
						re.lastIndex = 0;
						out = out.replace(re, rep);
					}
				}
			}
		}

		for (const [re, rep] of this.bareRes) {
			re.lastIndex = 0;
			out = out.replace(re, rep);
		}

		return out.trim();
	}

	/** 兼容字符串与多模态数组；只动 text part。 */
	sanitizeContent(content: unknown): [unknown, boolean] {
		if (typeof content === "string") {
			const out = this.sanitizeText(content);
			return [out, out !== content];
		}
		if (Array.isArray(content)) {
			let changed = false;
			const out = content.map((part) => {
				if (part && typeof part === "object" && typeof (part as Record<string, unknown>).text === "string") {
					const rec = part as Record<string, unknown>;
					const next = this.sanitizeText(rec.text as string);
					if (next !== rec.text) {
						changed = true;
						return { ...rec, text: next };
					}
				}
				return part;
			});
			return [out, changed];
		}
		return [content, false];
	}

	/** 净化整个请求体，返回改动计数。 */
	sanitizeBody(body: unknown): { body: unknown; hits: number } {
		let hits = 0;
		const walk = (node: unknown): unknown => {
			if (Array.isArray(node)) return node.map(walk);
			if (node && typeof node === "object") {
				const out: Record<string, unknown> = {};
				for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
					if (k === "content" || k === "system") {
						const [nv, ch] = this.sanitizeContent(v);
						if (ch) hits++;
						out[k] = nv;
					} else {
						out[k] = walk(v);
					}
				}
				return out;
			}
			return node;
		};
		return { body: walk(body), hits };
	}
}

let cache: RulesFile | null | undefined;

export function loadRules(): RulesFile {
	if (cache === undefined) cache = readDataJson<RulesFile>("fingerprint_rules.json");
	return cache ?? { profiles: {}, identity_banners: {}, neutral_rewrite: { text: FALLBACK_NEUTRAL } };
}

export const FALLBACK_NEUTRAL =
	"You are a helpful assistant. Respond in the user's language, follow the user's instructions, and be direct and concise.";

export function buildProfile(name = "anthropic"): Profile {
	const raw = loadRules().profiles[name];
	if (!raw) return new Profile(name, { label: name, features: [], rewrites: [], strip_regex: [], bare_key_regex: [] });
	return new Profile(name, raw);
}

export function buildAll(): Record<string, Profile> {
	const out: Record<string, Profile> = {};
	for (const [name, raw] of Object.entries(loadRules().profiles)) out[name] = new Profile(name, raw);
	return out;
}

export function sanitizeText(text: string, profile = "anthropic"): string {
	return buildProfile(profile).sanitizeText(text);
}

/** 诊断：列出命中的指纹特征。 */
export function scanFingerprints(text: string, profile = "anthropic"): string[] {
	const p = buildProfile(profile);
	const hits = p.features.filter((f) => text.includes(f));
	for (const [re] of p.bareRes) {
		re.lastIndex = 0;
		const m = re.exec(text);
		if (m && !hits.includes(m[0])) hits.push(m[0]);
	}
	return hits;
}

export function neutralPrompt(): string {
	return loadRules().neutral_rewrite?.text ?? FALLBACK_NEUTRAL;
}
