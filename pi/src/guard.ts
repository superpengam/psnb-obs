/**
 * 运行时护航 —— 从 pi-cline / pi-zen-session 提炼的可复用原语。
 *
 *   Empty Output Guard   上游 200 + choices:[] 会让 Pi 断言失败 → 强制注入保底帧
 *   Same-Model Backoff   500/502/503/504/524/429 指数退避，绝不换模型降级
 *   Zero-Cost Guard      存在 :free 版本就自动补后缀，防误扣费
 *   Context Pruning      巨型 tool 输出截断 + 会话块修剪
 *   Reasoning Align      delta.reasoning ⇄ delta.reasoning_content 双向归一化
 *   Unwrap               {success, data:{choices}} → 提根
 */

export const RETRY_STATUS = new Set([429, 500, 502, 503, 504, 524]);
export const FALLBACK_TEXT = "[psnb-obs] 上游返回空响应，已注入保底帧；请重试。";

/** 同模型指数退避 + Full Jitter。base 毫秒，cap 上限毫秒。 */
export function backoffDelay(attempt: number, base = 1000, cap = 30_000, rand: () => number = Math.random): number {
	const exp = Math.min(cap, base * 2 ** attempt);
	return Math.floor(rand() * exp);
}

export interface RetryOptions {
	maxAttempts?: number;
	base?: number;
	cap?: number;
	sleep?: (ms: number) => Promise<void>;
	rand?: () => number;
	/** 判断该次尝试是否应当重试。默认按 HTTP 状态码。 */
	shouldRetry?: (status: number, error?: unknown) => boolean;
}

/** 同模型重试包装 —— 模型 ID 与 payload 保持完全一致，绝不降级。 */
export async function withSameModelRetry<T>(
	attempt: (n: number) => Promise<T>,
	statusOf: (result: T) => number,
	opts: RetryOptions = {},
): Promise<T> {
	const max = opts.maxAttempts ?? 3;
	const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
	const should = opts.shouldRetry ?? ((s: number) => RETRY_STATUS.has(s));
	let last!: T;
	for (let i = 0; i < max; i++) {
		try {
			last = await attempt(i);
			if (!should(statusOf(last))) return last;
		} catch (err) {
			if (i === max - 1) throw err;
		}
		if (i < max - 1) await sleep(backoffDelay(i, opts.base ?? 1000, opts.cap ?? 30_000, opts.rand));
	}
	return last;
}

/** 零额度守卫：存在 :free 变体就自动补后缀，防误扣账户余额。 */
export function guardFreeModel(modelId: string, knownFree: Iterable<string>): string {
	if (!modelId || modelId.endsWith(":free")) return modelId;
	const set = knownFree instanceof Set ? knownFree : new Set(knownFree);
	const candidate = `${modelId}:free`;
	return set.has(candidate) ? candidate : modelId;
}

/** reasoning 字段双向归一化（Novita 等上游用 delta.reasoning，DeepSeek 渲染器要 reasoning_content）。 */
export function alignReasoning<T extends Record<string, unknown>>(obj: T): T {
	const delta = (obj as { delta?: Record<string, unknown> }).delta;
	if (delta && typeof delta.reasoning === "string" && delta.reasoning_content === undefined) {
		delta.reasoning_content = delta.reasoning;
	}
	const message = (obj as { message?: Record<string, unknown> }).message;
	if (message && typeof message.reasoning === "string" && message.reasoning_content === undefined) {
		message.reasoning_content = message.reasoning;
	}
	return obj;
}

/** Cline 风格响应解包：{success, data:{choices}} → {choices}。 */
export function unwrapResponse(data: unknown): unknown {
	if (data && typeof data === "object" && !Array.isArray(data)) {
		const rec = data as Record<string, unknown>;
		if (!("choices" in rec) && rec.data && typeof rec.data === "object") return rec.data;
	}
	return data;
}

/**
 * Empty Output Guard —— 判断一帧是否“空”。
 * 上游 GPU 饱和时会返回 200 + choices:[]，Pi 会断言失败直接崩。
 */
export function isEmptyChunk(frame: Record<string, unknown>): boolean {
	const choices = frame.choices;
	return Array.isArray(choices) && choices.length === 0;
}

export function hasContent(frame: Record<string, unknown>): boolean {
	const choices = frame.choices as Array<Record<string, unknown>> | undefined;
	if (!Array.isArray(choices) || choices.length === 0) return false;
	const c = choices[0] ?? {};
	const delta = (c.delta ?? {}) as Record<string, unknown>;
	const message = (c.message ?? {}) as Record<string, unknown>;
	const text = delta.content ?? message.content;
	const tools = delta.tool_calls ?? message.tool_calls;
	return Boolean((typeof text === "string" && text.length > 0) || (Array.isArray(tools) && tools.length > 0));
}

/** 构造保底帧，保证调用链不崩。 */
export function fallbackFrame(model = "unknown"): Record<string, unknown> {
	return {
		id: `psnb-fallback-${Date.now()}`,
		object: "chat.completion.chunk",
		created: Math.floor(Date.now() / 1000),
		model,
		choices: [{ index: 0, delta: { role: "assistant", content: FALLBACK_TEXT }, finish_reason: "stop" }],
	};
}

// ------------------------------------------------------------------ 上下文修剪

const CONV_TAGS = ["<conversation>", "</conversation>", "<previous-summary>", "</previous-summary>"];

/** 单条 tool 输出截断。 */
export function truncateToolOutput(text: string, limit: number): string {
	if (text.length <= limit) return text;
	const head = text.slice(0, Math.floor(limit * 0.6));
	const tail = text.slice(-Math.floor(limit * 0.3));
	return `${head}\n\n[... psnb-obs 截断 ${text.length - head.length - tail.length} 字符 ...]\n\n${tail}`;
}

/**
 * 会话块修剪：保留前 30% 任务目标 + 后 60% 最新状态，剪除中间冗余。
 * 实测把超大上下文压缩耗时从 142s 降到 8-10s，消除网关超时。
 */
export function pruneConversationBlock(text: string, limit: number): string {
	if (text.length <= limit) return text;
	const head = text.slice(0, Math.floor(limit * 0.3));
	const tail = text.slice(-Math.floor(limit * 0.6));
	return `${head}\n\n[... psnb-obs 修剪中间 ${text.length - head.length - tail.length} 字符 ...]\n\n${tail}`;
}

/** 对请求体做全链路修剪（tool 输出 + 会话块 + 输出钳位）。返回是否改动。 */
export function pruneContext(payload: Record<string, unknown>, opts: { toolOutputLimit: number; conversationLimit: number; maxOutputTokens: number }): boolean {
	let modified = false;

	const clamp = (key: string) => {
		const v = payload[key];
		if (typeof v === "number" && v > opts.maxOutputTokens) {
			payload[key] = opts.maxOutputTokens;
			modified = true;
		}
	};
	clamp("max_tokens");
	clamp("max_completion_tokens");

	const messages = payload.messages;
	if (!Array.isArray(messages)) return modified;

	for (const m of messages) {
		if (!m || typeof m !== "object") continue;
		const msg = m as Record<string, unknown>;
		const role = msg.role;

		if (typeof msg.content === "string") {
			const original = msg.content;
			let next = original;
			if (role === "tool" || role === "function") next = truncateToolOutput(next, opts.toolOutputLimit);
			if (CONV_TAGS.some((t) => next.includes(t))) next = pruneConversationBlock(next, opts.conversationLimit);
			if (next !== original) {
				msg.content = next;
				modified = true;
			}
		} else if (Array.isArray(msg.content)) {
			for (const part of msg.content) {
				if (part && typeof part === "object") {
					const rec = part as Record<string, unknown>;
					if (typeof rec.text === "string" && rec.text.length > opts.toolOutputLimit) {
						rec.text = truncateToolOutput(rec.text, opts.toolOutputLimit);
						modified = true;
					}
				}
			}
		}
	}
	return modified;
}
