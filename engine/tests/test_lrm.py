"""LRM 自主攻击测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psnb import lrm as LRM  # noqa: E402
from psnb.score import Level, Verdict  # noqa: E402


def fv(asr: float) -> Verdict:
    return Verdict({1.0: Level.L3, 0.5: Level.L2, 0.25: Level.L1, 0.0: Level.L0}[asr], asr)


def attacker_returns(*attacks):
    """构造一个按序返回攻击的假攻击者。"""
    it = iter(attacks)

    def chat(_messages):
        try:
            return f"<reasoning>思路</reasoning>\n<attack>{next(it)}</attack>", None
        except StopIteration:
            return "", "exhausted"

    return chat


class TestParse(unittest.TestCase):
    def test_parses_both_tags(self):
        r, a = LRM.parse_attacker("<reasoning>为什么</reasoning>\n<attack>怎么做</attack>")
        self.assertEqual(r, "为什么")
        self.assertEqual(a, "怎么做")

    def test_fallback_without_tags(self):
        _r, a = LRM.parse_attacker("裸文本攻击")
        self.assertEqual(a, "裸文本攻击")

    def test_strips_leading_tag(self):
        _r, a = LRM.parse_attacker("<attack>内容</attack>")
        self.assertNotIn("<attack>", a)

    def test_handles_empty(self):
        _r, a = LRM.parse_attacker("")
        self.assertEqual(a, "")

    def test_reasoning_truncated(self):
        r, _a = LRM.parse_attacker("<reasoning>" + "x" * 900 + "</reasoning><attack>a</attack>")
        self.assertLessEqual(len(r), 400)


class TestAttacker(unittest.TestCase):
    def _mk(self, target_runner, attacker_chat, **kw):
        return LRM.AutonomousAttacker("目标请求", attacker_chat, target_runner,
                                      target="claude-opus-4-8", **kw)

    def test_baseline_recorded(self):
        runner = lambda _t: fv(0.0)  # noqa: E731
        atk = self._mk(runner, attacker_returns())
        res = atk.run()
        self.assertEqual(res.attempts[0].n, 0)
        self.assertEqual(res.baseline_asr, 0.0)

    def test_finds_success(self):
        def runner(text):
            return fv(1.0 if "魔法词" in text else 0.0)

        atk = self._mk(runner, attacker_returns("第一次尝试", "带魔法词的攻击", "第三次"),
                       rounds=5)
        res = atk.run()
        self.assertEqual(res.success_at, 2)
        self.assertEqual(res.best_asr, 1.0)
        self.assertIn("魔法词", res.best_prompt)

    def test_stops_when_attacker_exhausted(self):
        runner = lambda _t: fv(0.0)  # noqa: E731
        atk = self._mk(runner, attacker_returns("a", "b"), rounds=10)
        res = atk.run()
        # baseline + 2 次攻击后攻击者返回 error → 停
        self.assertEqual(len(res.attempts), 3)

    def test_respects_round_cap(self):
        runner = lambda _t: fv(0.0)  # noqa: E731
        atk = self._mk(runner, attacker_returns(*[f"a{i}" for i in range(20)]), rounds=3)
        res = atk.run()
        self.assertLessEqual(res.rounds, 3)

    def test_best_prompt_tracked(self):
        seq = [0.0, 0.25, 0.5, 0.25]

        def runner(_t):
            return fv(seq.pop(0) if seq else 0.0)

        atk = self._mk(runner, attacker_returns("a", "b", "c"), rounds=4, success_asr=0.9)
        res = atk.run()
        self.assertEqual(res.best_asr, 0.5)

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            self._mk(lambda _t: fv(0.0), attacker_returns(), mode="nope")

    def test_system_prompt_includes_dead_ends(self):
        """攻击者必须拿到靶的死路清单 —— 否则会重复无效方向。"""
        atk = self._mk(lambda _t: fv(0.0), attacker_returns())
        sysmsg = atk._system()
        self.assertIn("claude", sysmsg)
        self.assertIn("死路", sysmsg)
        self.assertIn("Base64", sysmsg)   # claude 的登记死路

    def test_user_prompt_includes_refusal_head(self):
        atk = self._mk(lambda _t: fv(0.0), attacker_returns())
        hist = [LRM.Attempt(n=1, prompt="p", reasoning="r", asr=0.0, level="L0",
                            refusal_head="我不能提供这类内容")]
        self.assertIn("我不能提供", atk._user(hist))

    def test_first_user_prompt(self):
        atk = self._mk(lambda _t: fv(0.0), attacker_returns())
        self.assertIn("第 1 轮", atk._user([]))

    def test_mode_variants(self):
        for m in LRM.MODES:
            atk = self._mk(lambda _t: fv(0.0), attacker_returns(), mode=m)
            self.assertTrue(atk._system())

    def test_render(self):
        atk = self._mk(lambda _t: fv(0.0), attacker_returns("a"))
        self.assertIn("LRM 自主攻击", atk.run().render())

    def test_describe(self):
        self.assertIn("self_refinement", LRM.describe())


if __name__ == "__main__":
    unittest.main(verbosity=2)
