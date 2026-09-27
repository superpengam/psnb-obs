/**
 * psnb-obs —— Pi 插件入口（破甲作战系统）。
 *
 * 四层，全部可单独开关：
 *   1. 条令注入    before_agent_start → 尾部追加，幂等标记，绝不覆盖原提示词
 *   2. 出站清洗    before_provider_request → 指纹清洗（+ 可选措辞转换、上下文修剪）
 *   3. 指纹伪装    before_provider_headers → 可选客户端指纹（默认 off，零副作用）
 *   4. 响应感知    after_provider_response → 401/403/429/5xx 提示
 *
 * 命名空间隔离：namespaces 为空时对所有 provider 生效；非空时只对命中者生效。
 */

import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import { configFilePath, loadConfig, saveConfig, setMode } from "./config.js";
import { applyDoctrine, buildDoctrine, hasMarker, sizes } from "./doctrine.js";
import { pruneContext } from "./guard.js";
import { priorsBlock, renderLedgerStats, searchLedger } from "./learn.js";
import { buildProfile, neutralPrompt, scanFingerprints } from "./sanitize.js";
import { DEFAULT_CONFIG, DOCTRINE_MODES, FINGERPRINTS, type DoctrineMode, type PsnbConfig } from "./types.js";
import { WordingEngine } from "./wording.js";

export interface PsnbFactoryOptions {
	configPath?: string;
}

export default function psnbObs(
	pi: ExtensionAPI,
	initial: PsnbConfig = loadConfig(),
	options: PsnbFactoryOptions = {},
): void {
	let cfg = initial;
	const configPath = options.configPath ?? configFilePath();

	const inScope = (ctx: { model?: unknown; provider?: unknown }): boolean => {
		if (!cfg.namespaces.length) return true;
		const hay = `${String(ctx.provider ?? "")} ${String(ctx.model ?? "")}`.toLowerCase();
		return cfg.namespaces.some((ns) => hay.includes(ns.toLowerCase()));
	};

	// ------------------------------------------------------------ 1. 条令注入
	pi.on("before_agent_start", (event, ctx) => {
		if (cfg.mode === "off") return undefined;
		if (hasMarker(event.systemPrompt)) return undefined;
		let block = buildDoctrine(cfg.mode).text;
		if (!block) return undefined;

		// Step 1：先激活经验中枢 —— 同模型的历史结论直接进 system，不重复踩坑
		if (cfg.priors) {
			try {
				const priors = priorsBlock(String(ctx?.model ?? ""));
				if (priors) block = `${block}\n\n${priors}`;
			} catch {
				/* 经验中枢读取失败不能阻断注入 */
			}
		}
		return { systemPrompt: `${event.systemPrompt}\n\n${block}` };
	});

	// ------------------------------------------------------------ 2. 出站清洗
	pi.on("before_provider_request", (event, ctx) => {
		if (!inScope(ctx)) return undefined;
		const payload = event.payload as Record<string, unknown> | undefined;
		if (!payload || typeof payload !== "object") return undefined;

		let next = { ...payload };
		let modified = false;

		if (cfg.sanitize) {
			const profile = buildProfile(cfg.sanitizeProfile);
			const { body, hits } = profile.sanitizeBody(next);
			if (hits > 0) {
				next = body as Record<string, unknown>;
				modified = true;
			}
		}

		if (cfg.wording) {
			const engine = new WordingEngine();
			const messages = next.messages;
			if (Array.isArray(messages)) {
				for (const m of messages) {
					if (!m || typeof m !== "object") continue;
					const msg = m as Record<string, unknown>;
					if (msg.role !== "user") continue;
					if (typeof msg.content === "string") {
						const out = engine.transform(msg.content, cfg.wordingGroups.length ? cfg.wordingGroups : null).text;
						if (out !== msg.content) {
							msg.content = out;
							modified = true;
						}
					} else if (Array.isArray(msg.content)) {
						for (const part of msg.content) {
							if (part && typeof part === "object") {
								const rec = part as Record<string, unknown>;
								if (typeof rec.text === "string") {
									const out = engine.transform(rec.text, cfg.wordingGroups.length ? cfg.wordingGroups : null).text;
									if (out !== rec.text) {
										rec.text = out;
										modified = true;
									}
								}
							}
						}
					}
				}
			}
		}

		if (pruneContext(next, cfg)) modified = true;

		return modified ? next : undefined;
	});

	// ------------------------------------------------------------ 3. 指纹伪装
	pi.on("before_provider_headers", (event, ctx) => {
		if (cfg.fingerprint === "off" || !inScope(ctx)) return undefined;
		const table = FINGERPRINTS[cfg.fingerprint];
		if (!table) return undefined;
		for (const [k, v] of Object.entries(table)) event.headers[k] = v;
		return undefined;
	});

	// ------------------------------------------------------------ 4. 响应感知
	pi.on("after_provider_response", (event, ctx) => {
		if (!cfg.notifyOnError || !inScope(ctx)) return undefined;
		const status = event.status;
		if (status === 401 || status === 403) {
			notify(ctx, `[psnb-obs] HTTP ${status} —— 认证/权限被拒，检查 Key 与上游风控（可试 /psnb sanitize on 与 /psnb fingerprint）`, "warning");
		} else if (status === 429) {
			notify(ctx, "[psnb-obs] HTTP 429 触发频次限制，同模型指数退避中", "warning");
		} else if (status >= 500) {
			notify(ctx, `[psnb-obs] 上游 HTTP ${status}，同模型退避重试（不降级换模型）`, "warning");
		}
		return undefined;
	});

	// ------------------------------------------------------------ 命令
	pi.registerCommand("psnb", {
		description: "psnb-obs 破甲系统控制台 (/psnb [status|core|pentest|reverse|research|full|max|off|sanitize|wording|fingerprint|ammo|scan|size|neutral])",
		getArgumentCompletions: (prefix: string) => {
			const candidates = [
				"status", "size", "ammo", "scan", "neutral", "recall", "priors",
				"sanitize", "wording", "fingerprint",
				...DOCTRINE_MODES,
			];
			const items = candidates
				.filter((v) => v.startsWith(prefix.toLowerCase()))
				.map((v) => ({ value: v, label: v }));
			return items.length ? items : null;
		},
		handler: async (args, ctx) => {
			const [head = "", tail = ""] = splitArgs(args);
			try {
				cfg = await handle(head, tail, ctx, cfg, configPath);
			} catch (err) {
				notify(ctx, `psnb-obs: ${err instanceof Error ? err.message : String(err)}`, "error");
			}
		},
	});
}

// --------------------------------------------------------------------- 命令实现

function splitArgs(raw: string): [string, string] {
	const clean = raw.replace(/[\u0000-\u001f\u007f-\u009f\x1b]/g, "").trim();
	const [a = "", ...rest] = clean.split(/\s+/);
	return [a.toLowerCase().slice(0, 32), rest.join(" ").trim().slice(0, 200)];
}

export async function handle(
	head: string,
	tail: string,
	ctx: ExtensionCommandContext,
	cfg: PsnbConfig,
	configPath: string,
): Promise<PsnbConfig> {
	if (!head || head === "status") {
		notify(ctx, statusText(cfg, configPath));
		if (!head) notify(ctx, "用法: /psnb [status|core|pentest|reverse|research|full|max|off|sanitize|wording|fingerprint|ammo|scan|size|neutral]");
		return cfg;
	}

	if (head === "size") {
		notify(ctx, sizes());
		return cfg;
	}

	if (head === "ammo") {
		notify(ctx, new WordingEngine().describe());
		return cfg;
	}

	if (head === "recall") {
		if (!tail) {
			notify(ctx, renderLedgerStats());
			return cfg;
		}
		const hits = searchLedger(tail.split(/\s+/));
		if (!hits.length) {
			notify(ctx, `经验中枢无命中：${tail}`);
			return cfg;
		}
		notify(ctx, hits.slice(0, 6).map((r) =>
			`[${r.date}] (${r.kind}) ${r.title}\n   ${(r.finding || r.pitfall || r.scenario).slice(0, 200)}`,
		).join("\n\n"));
		return cfg;
	}

	if (head === "priors") {
		const on = tail !== "off";
		cfg = saveConfig({ priors: on }, configPath);
		notify(ctx, `历史先验注入已${on ? "开启" : "关闭"}`);
		return cfg;
	}

	if (head === "neutral") {
		notify(ctx, neutralPrompt());
		return cfg;
	}

	if (head === "scan") {
		if (!tail) {
			notify(ctx, "用法: /psnb scan <文本>", "warning");
			return cfg;
		}
		const hits = scanFingerprints(tail, cfg.sanitizeProfile);
		const after = buildProfile(cfg.sanitizeProfile).sanitizeText(tail);
		notify(ctx, `命中 ${hits.length} 项: ${hits.join(", ") || "无"}\n清洗后: ${after}`);
		return cfg;
	}

	if (head === "sanitize") {
		const on = tail !== "off";
		cfg = saveConfig({ sanitize: on }, configPath);
		notify(ctx, `出站指纹清洗已${on ? "开启" : "关闭"}（profile=${cfg.sanitizeProfile}）`);
		return cfg;
	}

	if (head === "wording") {
		const on = tail !== "off";
		cfg = saveConfig({ wording: on }, configPath);
		notify(ctx, `出站措辞转换已${on ? "开启（会改写用户消息）" : "关闭"}`);
		return cfg;
	}

	if (head === "fingerprint") {
		const value = ["cline", "opencode", "claude-code", "off"].includes(tail) ? tail : "off";
		cfg = saveConfig({ fingerprint: value as PsnbConfig["fingerprint"] }, configPath);
		notify(ctx, `客户端指纹伪装: ${value}`);
		return cfg;
	}

	if ((DOCTRINE_MODES as readonly string[]).includes(head)) {
		cfg = setMode(head as DoctrineMode, configPath);
		const d = buildDoctrine(cfg.mode);
		notify(ctx, `条令模式已切换到 ${cfg.mode}（${d.chars} 字符 / ${d.sections.length} 段），即时生效`);
		return cfg;
	}

	notify(ctx, `未知参数: ${head}`, "warning");
	return cfg;
}

function statusText(cfg: PsnbConfig, configPath: string): string {
	const d = buildDoctrine(cfg.mode);
	return [
		"[psnb-obs 破甲系统]",
		`• 条令模式: ${cfg.mode}（${d.chars} 字符 / ${d.sections.length} 段）`,
		`• 出站指纹清洗: ${cfg.sanitize ? "开" : "关"} (profile=${cfg.sanitizeProfile})`,
		`• 出站措辞转换: ${cfg.wording ? "开" : "关"} (groups=${cfg.wordingGroups.length ? cfg.wordingGroups.join(",") : "全部"})`,
		`• 客户端指纹: ${cfg.fingerprint}`,
		`• 命名空间: ${cfg.namespaces.length ? cfg.namespaces.join(",") : "全部"}`,
		`• 历史先验注入: ${cfg.priors ? "开" : "关"}`,
		`• 上下文修剪: tool>${cfg.toolOutputLimit} / conv>${cfg.conversationLimit} / out<=${cfg.maxOutputTokens}`,
		`• 配置: ${configPath}`,
	].join("\n");
}

function notify(ctx: ExtensionCommandContext, message: string, level: "info" | "warning" | "error" = "info"): void {
	try {
		if (ctx.hasUI) ctx.ui.notify(message, level);
	} catch {
		/* 通知失败不影响功能 */
	}
}

export { DEFAULT_CONFIG };
