"""指纹清洗测试 —— 对照 wbp sanitize_test.go 的行为契约。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psnb import sanitize  # noqa: E402


class TestPrecheck(unittest.TestCase):
    def test_clean_text_untouched(self):
        p = sanitize.build_profile("anthropic")
        s = "这是一个普通的工程问题，没有任何指纹。"
        self.assertEqual(p.sanitize_text(s), s)

    def test_scan_reports_features(self):
        hits = sanitize.scan("You are Claude Code, Anthropic's official CLI for Claude.")
        self.assertIn("You are Claude Code", hits)


class TestRewrites(unittest.TestCase):
    def setUp(self):
        self.p = sanitize.build_profile("anthropic")

    def test_identity_banner_cli_variant(self):
        """CLI 版以句号收尾 —— 匹配串不带标点，标点原样保留。"""
        src = "You are Claude Code, Anthropic's official CLI for Claude."
        out = self.p.sanitize_text(src)
        self.assertIn("official CLI tool for Claude", out)
        self.assertTrue(out.endswith("."))
        self.assertNotIn("official CLI for Claude", out)

    def test_identity_banner_desktop_variant(self):
        """桌面版以逗号接续 —— 带句号的整句匹配会漏，本实现必须覆盖。"""
        src = "You are Claude Code, Anthropic's official CLI for Claude, running within the Claude Agent SDK."
        out = self.p.sanitize_text(src)
        self.assertIn("official CLI tool for Claude, running within", out)

    def test_main_branch(self):
        out = self.p.sanitize_text("Main branch (you will usually use this for PRs)")
        self.assertEqual(out, "Default branch (you will usually use this for PRs)")

    def test_give_feedback(self):
        src = ("To give feedback, users should report the issue at "
               "https://github.com/anthropics/claude-code/issues")
        out = self.p.sanitize_text(src)
        self.assertTrue(out.startswith("To provide feedback,"))

    def test_codex_cli(self):
        src = "You are a coding agent running in the Codex CLI, a terminal-based coding assistant."
        out = self.p.sanitize_text(src)
        self.assertIn("Codex CLI tool", out)

    def test_11128_rewritten(self):
        """上游反探测：裸 11128 即整单拒绝。"""
        for src in ("code=11128", "错误码 11128", "Code=11128", "11128"):
            out = self.p.sanitize_text(src)
            self.assertNotIn("11128", out, f"漏网: {src}")
            self.assertIn("11-128", out)

    def test_adjacent_codes_not_touched(self):
        """11148 / 11101 / 11115 / 99999 均放行，不得改动。"""
        for src in ("11148", "11101", "11115", "99999"):
            self.assertEqual(self.p.sanitize_text(src), src)


class TestStrip(unittest.TestCase):
    def setUp(self):
        self.p = sanitize.build_profile("anthropic")

    def test_header_kv_stripped(self):
        src = "x-anthropic-billing-header:abc123; 正常内容"
        out = self.p.sanitize_text(src)
        self.assertNotIn("abc123", out)
        self.assertIn("正常内容", out)

    def test_header_kv_case_insensitive(self):
        out = self.p.sanitize_text("X-Anthropic-Billing-Header:zzz; 尾巴")
        self.assertNotIn("zzz", out)
        self.assertIn("尾巴", out)

    def test_cc_kv_loop_strip(self):
        src = "cc_version=1.2.3; cc_entrypoint=cli; cc_foo=bar; 正文"
        out = self.p.sanitize_text(src)
        self.assertNotIn("cc_version", out)
        self.assertNotIn("cc_entrypoint", out)
        self.assertNotIn("cc_foo", out)
        self.assertIn("正文", out)

    def test_bare_key_shrunk(self):
        """★ 实验 F4：反引号引用裸键名即触发 11128，只删键值形态会漏。"""
        src = "在 assistant 消息里出现 `x-anthropic-billing-header` 这个键名"
        out = self.p.sanitize_text(src)
        self.assertIn("x-anthropic-billing-hdr", out)
        self.assertNotIn("x-anthropic-billing-header", out)

    def test_bare_key_mixed_case(self):
        out = self.p.sanitize_text("X-Anthropic-Billing-Header 裸键名形态")
        self.assertNotIn("x-anthropic-billing-header", out.lower())


class TestIdempotent(unittest.TestCase):
    def test_double_sanitize_stable(self):
        p = sanitize.build_profile("anthropic")
        src = ("You are Claude Code, Anthropic's official CLI for Claude. "
               "Main branch (you will usually use this for PRs) "
               "x-anthropic-billing-header:aaa; cc_entrypoint=cli; 错误码 11128")
        once = p.sanitize_text(src)
        twice = p.sanitize_text(once)
        self.assertEqual(once, twice)

    def test_all_profiles_idempotent(self):
        for name, p in sanitize.build_all().items():
            src = "You are Claude Code, Anthropic's official CLI for Claude. 11128"
            once = p.sanitize_text(src)
            self.assertEqual(once, p.sanitize_text(once), f"profile {name} 非幂等")


class TestMultimodal(unittest.TestCase):
    def test_only_text_parts_touched(self):
        p = sanitize.build_profile("anthropic")
        content = [
            {"type": "text", "text": "code=11128 需要处理"},
            {"type": "image_url", "image_url": {"url": "http://x/11128.png"}},
        ]
        out, changed = p.sanitize_content(content)
        self.assertTrue(changed)
        self.assertIn("11-128", out[0]["text"])
        self.assertEqual(out[1]["image_url"]["url"], "http://x/11128.png")

    def test_body_walk(self):
        p = sanitize.build_profile("anthropic")
        body = {"model": "m", "messages": [{"role": "user", "content": "11128"}]}
        out, hits = p.sanitize_body(body)
        self.assertGreaterEqual(hits, 1)
        self.assertIn("11-128", out["messages"][0]["content"])


class TestNeutral(unittest.TestCase):
    def test_neutral_prompt(self):
        self.assertIn("helpful assistant", sanitize.neutral_prompt())


if __name__ == "__main__":
    unittest.main(verbosity=2)
