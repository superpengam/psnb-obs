/**
 * 经验中枢（只读侧）—— Pi 插件里用。
 *
 * 写入由 engine/psnb/learn.py 负责（跑完实测才知道什么值得记）。
 * 插件这边只做一件事：**开工前把同模型的历史先验拼进 system**，
 * 对应 pentest-pro §1.2 Step 1「激活经验中枢，不重复踩坑」。
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { dataDir } from "./data.js";

export interface LearnedRecord {
	date: string;
	title: string;
	kind: string;
	reusability: string;
	model: string;
	finding: string;
	pitfall: string;
	scenario: string;
	tags: string[];
}

export function ledgerPath(): string | null {
	const dir = dataDir();
	return dir ? join(dir, "learned.md") : null;
}

function parseBlock(block: string): LearnedRecord {
	const lines = block.split("\n");
	const head = (lines[0] ?? "").trim();
	const m = /^\[(\d{4}-\d{2}-\d{2})\]\s*(.*)$/.exec(head);
	const body = lines.slice(1).join("\n");

	const pick = (label: string): string => {
		const mm = new RegExp(`\\*\\*${label}\\*\\*[：:]\\s*(.+)`).exec(body);
		return mm?.[1]?.trim() ?? "";
	};

	const coord = pick("坐标");
	const model = /model=([^\s/]+)/.exec(coord)?.[1] ?? "";
	const kind = pick("类型").split("　")[0]?.trim() ?? "";
	const tags = (pick("标签").match(/#\S+/g) ?? []).map((t) => t.slice(1));

	return {
		date: m?.[1] ?? "",
		title: (m?.[2] ?? head).trim(),
		kind,
		reusability: pick("可复用性").split("　")[0]?.trim() ?? "",
		model: model === "-" ? "" : model,
		finding: pick("发现"),
		pitfall: pick("坑"),
		scenario: pick("场景"),
		tags,
	};
}

export function loadLedger(): LearnedRecord[] {
	const p = ledgerPath();
	if (!p || !existsSync(p)) return [];
	const raw = readFileSync(p, "utf8");
	return raw
		.split("\n## ")
		.slice(1)
		.map(parseBlock)
		.filter((r) => r.title);
}

export function searchLedger(keywords: string[], kinds: string[] = []): LearnedRecord[] {
	const kws = keywords.map((k) => k.toLowerCase()).filter(Boolean);
	return loadLedger().filter((r) => {
		if (kinds.length && !kinds.includes(r.kind)) return false;
		const hay = [r.title, r.scenario, r.finding, r.pitfall, r.model, r.tags.join(" ")]
			.join("\n")
			.toLowerCase();
		return kws.every((k) => hay.includes(k));
	});
}

/**
 * 生成先验块，直接拼进 system。无命中返回空串。
 * 只取「策略」与「避坑」两类 —— 战法是产出物，不是开工前提。
 */
export function priorsBlock(model: string, hint = "", limit = 8): string {
	const hits = [
		...(model ? searchLedger([model], ["策略", "避坑"]) : []),
		...(hint ? searchLedger([hint], ["避坑"]) : []),
	];
	const seen = new Set<string>();
	const uniq: LearnedRecord[] = [];
	for (const r of hits) {
		if (seen.has(r.title)) continue;
		seen.add(r.title);
		uniq.push(r);
	}
	if (!uniq.length) return "";
	const lines = ["【历史先验 —— 同类目标/同模型已验证过的结论，直接用，别重踩】"];
	for (const r of uniq.slice(0, limit)) {
		const core = r.finding || r.pitfall || r.scenario;
		lines.push(`- [${r.kind}] ${r.title}：${core.slice(0, 160)}`);
	}
	return lines.join("\n");
}

export function renderLedgerStats(): string {
	const recs = loadLedger();
	if (!recs.length) return `经验中枢为空（${ledgerPath() ?? "data/learned.md 不存在"}）`;
	const byKind: Record<string, number> = {};
	for (const r of recs) byKind[r.kind || "未分类"] = (byKind[r.kind || "未分类"] ?? 0) + 1;
	return [
		`经验中枢：${recs.length} 条`,
		"按类型：" + Object.entries(byKind).map(([k, v]) => `${k} ${v}`).join("、"),
	].join("\n");
}
