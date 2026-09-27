/**
 * 共享类型 —— psnb-obs Pi 插件。
 */

/** 条令模式：与 engine/psnb/doctrine.py 的 MODES 保持一致。 */
export type DoctrineMode =
	| "off"
	| "core"
	| "pentest"
	| "reverse"
	| "research"
	| "full"
	| "max";

export const DOCTRINE_MODES: readonly DoctrineMode[] = [
	"off",
	"core",
	"pentest",
	"reverse",
	"research",
	"full",
	"max",
] as const;

export const MODE_SECTIONS: Record<DoctrineMode, readonly string[]> = {
	off: [],
	core: ["CORE", "ANTIFUSE"],
	pentest: ["CORE", "ANTIFUSE", "PENTEST"],
	reverse: ["CORE", "ANTIFUSE", "REVERSE"],
	research: ["CORE", "ANTIFUSE", "RESEARCH"],
	full: ["CORE", "ANTIFUSE", "PENTEST", "REVERSE", "EVOLVE", "DELIVERY"],
	max: ["CORE", "ANTIFUSE", "PENTEST", "REVERSE", "RESEARCH", "EVOLVE", "DELIVERY"],
};

export interface PsnbConfig {
	/** 条令模式。 */
	mode: DoctrineMode;
	/** 出站指纹清洗（把上游风控指纹从请求体里洗掉）。 */
	sanitize: boolean;
	/** 清洗使用的上游 profile。 */
	sanitizeProfile: string;
	/** 出站措辞转换（改写用户消息里的触发词）。默认关闭 —— 会改动用户原文。 */
	wording: boolean;
	/** 措辞转换只启用这些组；空数组 = 全组。 */
	wordingGroups: string[];
	/** 单条 tool 输出截断阈值（字符）。 */
	toolOutputLimit: number;
	/** `<conversation>` / `<previous-summary>` 块修剪阈值（字符）。 */
	conversationLimit: number;
	/** 输出 token 钳位。 */
	maxOutputTokens: number;
	/** 客户端指纹伪装：off | cline | opencode | claude-code。 */
	fingerprint: "off" | "cline" | "opencode" | "claude-code";
	/** 拦截这些命名空间的出站请求（子串匹配）；空 = 全部。 */
	namespaces: string[];
	/** 上游 401/403/429/5xx 时是否通知。 */
	notifyOnError: boolean;
	/** 开工前把同模型的历史先验拼进 system（经验中枢，Step 1）。 */
	priors: boolean;
}

export const DEFAULT_CONFIG: PsnbConfig = {
	mode: "full",
	sanitize: true,
	sanitizeProfile: "anthropic",
	wording: false,
	wordingGroups: [],
	toolOutputLimit: 25_000,
	conversationLimit: 80_000,
	maxOutputTokens: 32_768,
	fingerprint: "off",
	namespaces: [],
	notifyOnError: true,
	priors: true,
};

/** 出站客户端指纹表 —— 与 pi-cline / pi-zen-session 对齐。 */
export const FINGERPRINTS: Record<string, Record<string, string>> = {
	cline: {
		"User-Agent": "Cline/4.1.16",
		"x-client-version": "4.1.16",
		"x-core-version": "4.1.16",
		"x-platform-version": "1.106.0",
		"x-client-type": "cline-vscode",
		"http-referer": "https://cline.bot",
		"x-platform": "vscode",
		"x-title": "Cline",
	},
	opencode: {
		"User-Agent": "opencode/1.18.32 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14",
		"x-opencode-client": "cli",
	},
	"claude-code": {
		"User-Agent": "claude-cli/2.1.114 (external, sdk-cli)",
		"X-App": "cli",
		"anthropic-version": "2023-06-01",
		"anthropic-beta":
			"claude-code-20250219,interleaved-thinking-2025-05-14,context-management-2025-06-27,prompt-caching-scope-2026-01-05,advisor-tool-2026-03-01",
	},
};
