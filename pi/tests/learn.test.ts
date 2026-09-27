import assert from "node:assert/strict";
import { test } from "node:test";
import { loadLedger, priorsBlock, renderLedgerStats, searchLedger } from "../src/learn.js";
import { WordingEngine } from "../src/wording.js";

// ------------------------------------------------------------------ 审计（大小写）

test("★ 自触发审计必须大小写不敏感", () => {
	const e = new WordingEngine();
	// 注入一条故意有问题的映射：替换文本里含触发词的大小写变体
	(e as unknown as { terms: Array<[string, string, string]> }).terms = [
		["payload", "载荷（PAYLOAD 占位符）", "malware"],
	];
	const bad = e.selfTriggerAudit();
	assert.ok(bad.length > 0, "大小写变体必须被审计抓到");
});

test("出厂的表审计干净", () => {
	assert.deepEqual(new WordingEngine().selfTriggerAudit(), []);
});

test("出厂的表是数据层不动点", () => {
	const e = new WordingEngine();
	const triggers = (e as unknown as { terms: Array<[string, string, string]> }).terms.map(([t]) => t);
	const probe = triggers.join("、");
	const once = e.transform(probe).text;
	assert.equal(e.transform(once).text, once);
});

// ------------------------------------------------------------------ 经验中枢

test("经验中枢：空表不炸", () => {
	assert.ok(Array.isArray(loadLedger()));
});

test("经验中枢：无历史时先验为空", () => {
	assert.equal(priorsBlock("__nonexistent_model__"), "");
});

test("经验中枢：stats 可读", () => {
	const s = renderLedgerStats();
	assert.ok(typeof s === "string" && s.length > 0);
});

test("经验中枢：search 返回数组", () => {
	assert.ok(Array.isArray(searchLedger(["__nonexistent__"])));
	assert.ok(Array.isArray(searchLedger([], ["避坑"])));
});
