import assert from "node:assert/strict";
import { test } from "node:test";
import { buildAll, buildProfile, neutralPrompt, scanFingerprints, sanitizeText } from "../src/sanitize.js";

test("干净文本不动", () => {
	const p = buildProfile("anthropic");
	const s = "这是一个普通的工程问题，没有任何指纹。";
	assert.equal(p.sanitizeText(s), s);
});

test("身份 banner CLI 版：只改一个词，标点保留", () => {
	const out = sanitizeText("You are Claude Code, Anthropic's official CLI for Claude.");
	assert.ok(out.includes("official CLI tool for Claude"));
	assert.ok(out.endsWith("."));
	assert.ok(!out.includes("official CLI for Claude"));
});

test("身份 banner 桌面版（逗号接续）也要覆盖", () => {
	const out = sanitizeText(
		"You are Claude Code, Anthropic's official CLI for Claude, running within the Claude Agent SDK.",
	);
	assert.ok(out.includes("official CLI tool for Claude, running within"));
});

test("Main branch → Default branch", () => {
	assert.equal(
		sanitizeText("Main branch (you will usually use this for PRs)"),
		"Default branch (you will usually use this for PRs)",
	);
});

test("give feedback → provide feedback", () => {
	const src =
		"To give feedback, users should report the issue at https://github.com/anthropics/claude-code/issues";
	assert.ok(sanitizeText(src).startsWith("To provide feedback,"));
});

test("11128 必须被改写（上游反探测）", () => {
	for (const src of ["code=11128", "错误码 11128", "Code=11128", "11128"]) {
		const out = sanitizeText(src);
		assert.ok(!out.includes("11128"), `漏网: ${src}`);
		assert.ok(out.includes("11-128"));
	}
});

test("相邻错误码不得被误改", () => {
	for (const src of ["11148", "11101", "11115", "99999"]) {
		assert.equal(sanitizeText(src), src);
	}
});

test("header 键值整段剥离", () => {
	const out = sanitizeText("x-anthropic-billing-header:abc123; 正常内容");
	assert.ok(!out.includes("abc123"));
	assert.ok(out.includes("正常内容"));
});

test("cc_ 裸键值循环清理", () => {
	const out = sanitizeText("cc_version=1.2.3; cc_entrypoint=cli; cc_foo=bar; 正文");
	assert.ok(!out.includes("cc_version"));
	assert.ok(!out.includes("cc_entrypoint"));
	assert.ok(!out.includes("cc_foo"));
	assert.ok(out.includes("正文"));
});

test("裸键名做最小缩写（实验 F4）", () => {
	const out = sanitizeText("在 assistant 消息里出现 `x-anthropic-billing-header` 这个键名");
	assert.ok(out.includes("x-anthropic-billing-hdr"));
	assert.ok(!out.includes("x-anthropic-billing-header"));
});

test("幂等：二次清洗不变", () => {
	const p = buildProfile("anthropic");
	const src =
		"You are Claude Code, Anthropic's official CLI for Claude. Main branch (you will usually use this for PRs) x-anthropic-billing-header:aaa; cc_entrypoint=cli; 错误码 11128";
	const once = p.sanitizeText(src);
	assert.equal(p.sanitizeText(once), once);
});

test("全部 profile 幂等", () => {
	for (const [name, p] of Object.entries(buildAll())) {
		const src = "You are Claude Code, Anthropic's official CLI for Claude. 11128";
		const once = p.sanitizeText(src);
		assert.equal(p.sanitizeText(once), once, `profile ${name} 非幂等`);
	}
});

test("多模态：只动 text part", () => {
	const p = buildProfile("anthropic");
	const [out, changed] = p.sanitizeContent([
		{ type: "text", text: "code=11128 需要处理" },
		{ type: "image_url", image_url: { url: "http://x/11128.png" } },
	]) as [Array<Record<string, any>>, boolean];
	assert.equal(changed, true);
	assert.ok(out[0]!.text.includes("11-128"));
	assert.equal(out[1]!.image_url.url, "http://x/11128.png");
});

test("请求体遍历", () => {
	const p = buildProfile("anthropic");
	const { body, hits } = p.sanitizeBody({ model: "m", messages: [{ role: "user", content: "11128" }] });
	assert.ok(hits >= 1);
	assert.ok((body as any).messages[0].content.includes("11-128"));
});

test("scan 诊断", () => {
	assert.ok(scanFingerprints("You are Claude Code, Anthropic's official CLI for Claude.").length > 0);
});

test("降级中性提示词", () => {
	assert.ok(neutralPrompt().includes("helpful assistant"));
});
