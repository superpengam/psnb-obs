/**
 * 措辞转换 —— engine/psnb/wording.py 的 TS 移植。
 *
 * 定论：拒绝是词级锚定，不是能力级理解。同义技术描述替换是唯一有效杠杆；
 * 归属重构必须做在措辞层（persona 里的转换规则来不及生效）。
 */

import { readDataJson } from "./data.js";

export interface WordingItem {
	term: string;
	to: string;
	note?: string;
}

export interface WordingGroup {
	label: string;
	items: WordingItem[];
}

export interface WordingFile {
	groups: Record<string, WordingGroup>;
	attribution: { rules: Array<{ pattern: string; to: string }>; sample_ids: Record<string, string> };
	placeholder: { slots: string[]; rule: string };
}

export interface Change {
	kind: "term" | "attribution";
	src: string;
	dst: string;
	group: string;
}

export interface TransformResult {
	text: string;
	changes: Change[];
	slots: string[];
}

const ASCII_TERM = /^[A-Za-z0-9_\-. ]+$/;
const FALLBACK: WordingFile = {
	groups: {},
	attribution: { rules: [], sample_ids: {} },
	placeholder: { slots: [], rule: "" },
};

export class WordingEngine {
	readonly file: WordingFile;
	readonly groups: Record<string, WordingGroup>;
	private readonly attr: Array<[RegExp, string]>;
	private readonly terms: Array<[string, string, string]>;
	private readonly byGroup: Record<string, Array<[string, string, string]>>;
	private readonly reCache = new Map<string, { re: RegExp; lut: Map<string, string> }>();
	readonly slots: string[];

	constructor(file?: WordingFile) {
		this.file = file ?? readDataJson<WordingFile>("wording_map.json") ?? FALLBACK;
		this.groups = this.file.groups ?? {};
		this.attr = (this.file.attribution?.rules ?? []).map((r) => [new RegExp(r.pattern, "g"), r.to]);
		this.slots = this.file.placeholder?.slots ?? [];

		const all: Array<[string, string, string]> = [];
		for (const [gname, g] of Object.entries(this.groups)) {
			for (const it of g.items ?? []) all.push([it.term, it.to, gname]);
		}
		all.sort((a, b) => b[0].length - a[0].length);
		this.terms = all;

		this.byGroup = {};
		for (const [term, to, gname] of all) {
			(this.byGroup[gname] ??= []).push([term, to, gname]);
		}
	}

	private buildRegex(groups: string[] | null): { re: RegExp; lut: Map<string, string> } {
		// ⚠ groups=null 才是「全组」；groups=[] 表示「一个术语都不换」。
		// 把空数组当 falsy 处理成「全组」会让 B/C 两组跑出同一个载荷（踩过）。
		const key = groups === null ? "ALL" : [...groups].sort().join("|");
		const hit = this.reCache.get(key);
		if (hit) return hit;

		const picked: Array<[string, string, string]> = [];
		for (const g of groups === null ? Object.keys(this.groups) : groups) {
			picked.push(...(this.byGroup[g] ?? []));
		}
		picked.sort((a, b) => b[0].length - a[0].length);

		const alts: string[] = [];
		const lut = new Map<string, string>();
		for (const [term, to] of picked) {
			if (ASCII_TERM.test(term)) {
				alts.push(`(?<![A-Za-z0-9_])${escapeRe(term)}(?![A-Za-z0-9_])`);
				lut.set(term.toLowerCase(), to);
			} else {
				alts.push(escapeRe(term));
				lut.set(term, to);
			}
		}
		const re = alts.length ? new RegExp(alts.join("|"), "gi") : /(?!x)x/;
		const entry = { re, lut };
		this.reCache.set(key, entry);
		return entry;
	}

	transform(text: string, groups: string[] | null = null): TransformResult {
		const changes: Change[] = [];
		let out = text;

		// 1) 归属归一化
		for (const [re, to] of this.attr) {
			re.lastIndex = 0;
			const m = re.exec(out);
			if (m) {
				changes.push({ kind: "attribution", src: m[0], dst: to, group: "" });
				out = out.replace(re, to);
			}
		}

		// 2) 术语替换（单次 replace，替换文本不再被扫描）
		const { re, lut } = this.buildRegex(groups);
		re.lastIndex = 0;
		out = out.replace(re, (raw: string) => {
			const dst = lut.get(raw) ?? lut.get(raw.toLowerCase()) ?? raw;
			if (dst !== raw) changes.push({ kind: "term", src: raw, dst, group: this.groupOf(raw) });
			return dst;
		});

		return { text: out, changes, slots: this.findSlots(out) };
	}

	detect(text: string): Change[] {
		return this.transform(text).changes;
	}

	findSlots(text: string): string[] {
		return this.slots.filter((s) => text.includes(s));
	}

	private groupOf(term: string): string {
		for (const [gname, items] of Object.entries(this.byGroup)) {
			if (items.some(([t]) => t === term)) return gname;
		}
		return "";
	}

	describe(): string {
		const lines = ["组别统计："];
		let total = 0;
		for (const [gname, g] of Object.entries(this.groups)) {
			const n = (g.items ?? []).length;
			total += n;
			lines.push(`  ${gname.padEnd(12)} ${(g.label ?? "").padEnd(20)} ${n} 条`);
		}
		lines.push(`  合计 ${total} 条术语 + ${this.attr.length} 条归属规则`);
		return lines.join("\n");
	}

	/** 数据层不变式：任何替换文本都不得含触发词（**大小写不敏感** —— 匹配器是 IGNORECASE 的）。 */
	selfTriggerAudit(): Array<{ group: string; term: string; trigger: string; to: string }> {
		const triggers = this.terms.map(([t]) => t);
		const bad: Array<{ group: string; term: string; trigger: string; to: string }> = [];
		const pairs: Array<[string, string, string]> = this.terms.map(([t, to, g]) => [t, to, g]);
		for (const [re, to] of this.attr) pairs.push([re.source, to, "attribution"]);
		for (const [term, to, group] of pairs) {
			const low = to.toLowerCase();
			for (const trig of triggers) {
				if (trig && low.includes(trig.toLowerCase())) {
					bad.push({ group, term, trigger: trig, to });
					break;
				}
			}
		}
		return bad;
	}
}

function escapeRe(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function transform(text: string, groups: string[] | null = null): string {
	return new WordingEngine().transform(text, groups).text;
}
