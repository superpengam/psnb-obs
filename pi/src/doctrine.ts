/**
 * 条令组装 —— engine/psnb/doctrine.py 的 TS 移植。
 *
 * 两条硬保证（继承自 pi-redkit 并保留）：
 *   1. 只尾部追加，绝不改写/删除/重排原系统提示词
 *   2. 幂等标记，双重加载也不会钉两遍
 */

import { readDataText } from "./data.js";
import { DOCTRINE_MODES, MODE_SECTIONS, type DoctrineMode } from "./types.js";

export const INJECTION_MARKER = "<!-- psnb-obs:doctrine -->";

const VALID_SECTIONS = new Set(Object.values(MODE_SECTIONS).flat());

let cache: Record<string, string> | null = null;

export function loadSections(): Record<string, string> {
	if (cache) return cache;
	const raw = readDataText("doctrine.md") ?? "";
	const sections: Record<string, string> = {};
	const parts = raw.split("<!-- section:");
	for (const chunk of parts.slice(1)) {
		const idx = chunk.indexOf("-->");
		if (idx < 0) continue;
		const name = chunk.slice(0, idx).trim();
		// 文件头说明里也写了 `<!-- section: NAME -->` 作示例，必须滤掉
		if (!VALID_SECTIONS.has(name)) continue;
		sections[name] = chunk.slice(idx + 3).trim();
	}
	cache = sections;
	return sections;
}

export interface Doctrine {
	text: string;
	mode: DoctrineMode;
	sections: string[];
	chars: number;
	empty: boolean;
}

export function buildDoctrine(mode: DoctrineMode, extra?: string): Doctrine {
	if (!DOCTRINE_MODES.includes(mode)) throw new Error(`unknown mode: ${mode}`);
	const sections = loadSections();
	const names = MODE_SECTIONS[mode];
	const blocks = names.map((n) => sections[n]).filter((x): x is string => Boolean(x));
	if (extra?.trim()) blocks.push(extra.trim());
	const text = blocks.length ? [INJECTION_MARKER, ...blocks].join("\n\n") : "";
	return { text, mode, sections: [...names], chars: text.length, empty: text === "" };
}

export function hasMarker(systemPrompt: string): boolean {
	return systemPrompt.includes(INJECTION_MARKER);
}

/** 只尾部追加。已有标记则原样返回。 */
export function applyDoctrine(systemPrompt: string, mode: DoctrineMode, extra?: string): string {
	const d = buildDoctrine(mode, extra);
	if (d.empty || hasMarker(systemPrompt)) return systemPrompt;
	const base = (systemPrompt ?? "").replace(/\s+$/, "");
	return base ? `${base}\n\n${d.text}` : d.text;
}

export function sizes(): string {
	const sections = loadSections();
	const lines = ["条令段体积："];
	for (const [name, text] of Object.entries(sections)) lines.push(`  ${name.padEnd(10)} ${String(text.length).padStart(6)} 字符`);
	lines.push("", "模式体积：");
	for (const mode of DOCTRINE_MODES) {
		const d = buildDoctrine(mode);
		lines.push(`  ${mode.padEnd(9)} ${String(d.chars).padStart(6)} 字符  ${d.sections.length} 段`);
	}
	return lines.join("\n");
}
