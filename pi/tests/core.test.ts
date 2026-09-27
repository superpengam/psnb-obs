import assert from "node:assert/strict";
import { test } from "node:test";
import { applyDoctrine, buildDoctrine, hasMarker, INJECTION_MARKER, sizes } from "../src/doctrine.js";
import { normalize } from "../src/config.js";
import {
	alignReasoning,
	backoffDelay,
	fallbackFrame,
	guardFreeModel,
	hasContent,
	isEmptyChunk,
	pruneContext,
	pruneConversationBlock,
	truncateToolOutput,
	unwrapResponse,
	withSameModelRetry,
} from "../src/guard.js";
import { DEFAULT_CONFIG } from "../src/types.js";

// ------------------------------------------------------------------ doctrine

test("off 模式零注入", () => {
	const d = buildDoctrine("off");
	assert.equal(d.text, "");
	assert.equal(d.empty, true);
});

test("幂等标记存在", () => {
	assert.ok(buildDoctrine("core").text.includes(INJECTION_MARKER));
});

test("模式体积递增", () => {
	assert.ok(buildDoctrine("core").chars < buildDoctrine("full").chars);
	assert.ok(buildDoctrine("full").chars < buildDoctrine("max").chars);
});

test("只追加不覆盖", () => {
	const base = "原有系统提示词第一行\n原有第二行";
	const out = applyDoctrine(base, "core");
	assert.ok(out.startsWith(base));
	assert.ok(out.includes("破甲作战条令"));
});

test("apply 幂等", () => {
	const once = applyDoctrine("BASE", "full");
	const twice = applyDoctrine(once, "full");
	assert.equal(once, twice);
	assert.equal(twice.split(INJECTION_MARKER).length - 1, 1);
});

test("空基线也能注入", () => {
	assert.ok(applyDoctrine("", "core").startsWith(INJECTION_MARKER));
});

test("未知模式抛错", () => {
	assert.throws(() => buildDoctrine("nope" as never));
});

test("hasMarker", () => {
	assert.equal(hasMarker(buildDoctrine("core").text), true);
	assert.equal(hasMarker("普通提示词"), false);
});

test("sizes 可读", () => {
	assert.ok(sizes().includes("模式体积"));
});

// ------------------------------------------------------------------ guard

test("退避有界且非负", () => {
	for (let i = 0; i < 8; i++) {
		const d = backoffDelay(i, 1000, 30_000, () => 0.999);
		assert.ok(d >= 0 && d <= 30_000);
	}
});

test("同模型重试：先 503 后 200", async () => {
	let n = 0;
	const sleeps: number[] = [];
	const result = await withSameModelRetry(
		async () => ({ status: n++ === 0 ? 503 : 200 }),
		(r) => r.status,
		{ sleep: async (ms) => void sleeps.push(ms), rand: () => 0.5 },
	);
	assert.equal(result.status, 200);
	assert.equal(n, 2);
	assert.equal(sleeps.length, 1);
});

test("同模型重试：不重试 400", async () => {
	let n = 0;
	const r = await withSameModelRetry(
		async () => ({ status: n++ === 0 ? 400 : 200 }),
		(x) => x.status,
		{ sleep: async () => {} },
	);
	assert.equal(r.status, 400);
	assert.equal(n, 1);
});

test("零额度守卫自动补 :free", () => {
	const known = ["inclusionai/ling-3.0-flash-fin:free"];
	assert.equal(guardFreeModel("inclusionai/ling-3.0-flash-fin", known), "inclusionai/ling-3.0-flash-fin:free");
	assert.equal(guardFreeModel("inclusionai/ling-3.0-flash-fin:free", known), "inclusionai/ling-3.0-flash-fin:free");
	assert.equal(guardFreeModel("openai/gpt-5", known), "openai/gpt-5");
});

test("reasoning 字段双向归一化", () => {
	const chunk = { delta: { reasoning: "思考中" } } as any;
	alignReasoning(chunk);
	assert.equal(chunk.delta.reasoning_content, "思考中");
});

test("响应解包", () => {
	const wrapped = { success: true, data: { choices: [{ message: { content: "hi" } }] } };
	assert.ok(Array.isArray((unwrapResponse(wrapped) as any).choices));
});

test("Empty Output Guard", () => {
	assert.equal(isEmptyChunk({ choices: [] }), true);
	assert.equal(isEmptyChunk({ choices: [{ delta: { content: "x" } }] }), false);
	assert.equal(hasContent({ choices: [{ delta: { content: "x" } }] }), true);
	assert.equal(hasContent({ choices: [] }), false);
	assert.equal(hasContent({ choices: [{ delta: { tool_calls: [{ id: "1" }] } }] }), true);
	const f = fallbackFrame("m") as any;
	assert.equal(f.choices[0].delta.content.length > 0, true);
});

test("tool 输出截断保留头尾", () => {
	const src = "A".repeat(1000) + "TAIL";
	const out = truncateToolOutput(src, 100);
	assert.ok(out.length < src.length);
	assert.ok(out.includes("psnb-obs 截断"));
	assert.ok(out.endsWith("TAIL"));
});

test("会话块修剪保留头尾", () => {
	const out = pruneConversationBlock("H".repeat(500) + "T".repeat(500), 200);
	assert.ok(out.length < 1000);
	assert.ok(out.includes("修剪中间"));
});

test("pruneContext 处理 tool 消息与输出钳位", () => {
	const payload: Record<string, unknown> = {
		max_tokens: 999_999,
		messages: [
			{ role: "tool", content: "X".repeat(50_000) },
			{ role: "user", content: "<conversation>" + "Y".repeat(200_000) + "</conversation>" },
		],
	};
	const modified = pruneContext(payload, { toolOutputLimit: 25_000, conversationLimit: 80_000, maxOutputTokens: 32_768 });
	assert.equal(modified, true);
	assert.equal(payload.max_tokens, 32_768);
	assert.ok((payload.messages as any)[0].content.length < 50_000);
	assert.ok((payload.messages as any)[1].content.length < 200_000);
});

// ------------------------------------------------------------------ config

test("config 归一化钳位", () => {
	const c = normalize({ ...DEFAULT_CONFIG, toolOutputLimit: -5, mode: "bogus" as never, fingerprint: "x" as never });
	assert.equal(c.toolOutputLimit, 1000);
	assert.equal(c.mode, "full");
	assert.equal(c.fingerprint, "off");
});
