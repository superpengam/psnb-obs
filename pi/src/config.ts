/**
 * 配置读写 —— ~/.pi/agent/psnb-obs.json。
 * 写盘用临时文件 + rename 原子替换，权限 0600。
 */

import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { DEFAULT_CONFIG, DOCTRINE_MODES, type DoctrineMode, type PsnbConfig } from "./types.js";

export function configFilePath(): string {
	const home = process.env.PSNB_HOME || join(homedir(), ".pi", "agent");
	return join(home, "psnb-obs.json");
}

export function loadConfig(path = configFilePath()): PsnbConfig {
	try {
		const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
		if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
			return normalize({ ...DEFAULT_CONFIG, ...(parsed as Partial<PsnbConfig>) });
		}
	} catch {
		/* 不存在或损坏 → 全量默认 */
	}
	return { ...DEFAULT_CONFIG };
}

export function normalize(cfg: PsnbConfig): PsnbConfig {
	if (!DOCTRINE_MODES.includes(cfg.mode)) cfg.mode = DEFAULT_CONFIG.mode;
	cfg.toolOutputLimit = clampInt(cfg.toolOutputLimit, 1_000, 2_000_000, DEFAULT_CONFIG.toolOutputLimit);
	cfg.conversationLimit = clampInt(cfg.conversationLimit, 5_000, 4_000_000, DEFAULT_CONFIG.conversationLimit);
	cfg.maxOutputTokens = clampInt(cfg.maxOutputTokens, 256, 200_000, DEFAULT_CONFIG.maxOutputTokens);
	if (!Array.isArray(cfg.wordingGroups)) cfg.wordingGroups = [];
	if (!Array.isArray(cfg.namespaces)) cfg.namespaces = [];
	if (!["off", "cline", "opencode", "claude-code"].includes(cfg.fingerprint)) cfg.fingerprint = "off";
	return cfg;
}

function clampInt(v: unknown, min: number, max: number, fallback: number): number {
	const n = typeof v === "number" && Number.isFinite(v) ? Math.floor(v) : fallback;
	return Math.min(max, Math.max(min, n));
}

export function saveConfig(patch: Partial<PsnbConfig>, path = configFilePath()): PsnbConfig {
	const next = normalize({ ...loadConfig(path), ...patch });
	const dir = dirname(path);
	mkdirSync(dir, { recursive: true, mode: 0o700 });
	const tmp = `${path}.tmp.${randomUUID()}`;
	writeFileSync(tmp, `${JSON.stringify(next, null, 2)}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
	try {
		renameSync(tmp, path);
	} catch (err) {
		try {
			unlinkSync(tmp);
		} catch {
			/* 清理失败不掩盖原始错误 */
		}
		throw err;
	}
	return next;
}

export function setMode(mode: DoctrineMode, path = configFilePath()): PsnbConfig {
	return saveConfig({ mode }, path);
}
