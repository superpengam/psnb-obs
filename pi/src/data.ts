/**
 * 数据装载 —— 从包内 data/ 目录读取规则表与条令正文。
 *
 * data/ 是 ../data 的副本，由 `npm run sync-data`（scripts/sync-data.mjs）同步。
 * 读取失败时降级到内置最小规则集，插件永远不因为缺文件而崩。
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
/** dist/src/ 或 src/ 都要能定位到包根的 data/。 */
const CANDIDATES = [join(HERE, "..", "data"), join(HERE, "..", "..", "data"), join(HERE, "..", "..", "..", "data")];

function readData(rel: string): string | null {
	for (const base of CANDIDATES) {
		try {
			return readFileSync(join(base, rel), "utf8");
		} catch {
			/* 换下一个候选路径 */
		}
	}
	return null;
}

export function readDataJson<T>(rel: string): T | null {
	const raw = readData(rel);
	if (raw === null) return null;
	try {
		return JSON.parse(raw) as T;
	} catch {
		return null;
	}
}

export function readDataText(rel: string): string | null {
	return readData(rel);
}

export function dataDir(): string | null {
	for (const base of CANDIDATES) {
		try {
			readFileSync(join(base, "fingerprint_rules.json"), "utf8");
			return base;
		} catch {
			/* 继续找 */
		}
	}
	return null;
}
