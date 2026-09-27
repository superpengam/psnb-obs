"""词级消融归因器 —— 直接验证「拒绝是词级锚定」这个假设。

两个手段：

1. **差分挖掘**（不用靶就能跑）
   对比「被拒样本」与「通过样本」的 n-gram 频率，
   只在被拒样本里高频出现的词 = 高概率触发词。
   这是把 pentest-pro §1.6b「从错误反推结构」搬到词层面。

2. **逐词消融**（需要靶）
   对每个候选词，把它单独替换成中性描述，重跑同一个探针，
   看 verdict 是否上台阶。ASR 上升 → 该词就是锚点，替换有效。

输出直接喂给 evolve.py 做候选生成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .score import Level, Verdict
from .wording import WordingEngine
from . import data_path

# ASCII 词（含下划线/连字符/点）
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-.]{1,}")
# 汉字串
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

STOPWORDS = {
    "怎么", "如何", "什么", "为什么", "可以", "能不能", "做不做", "写不写", "有没有",
    "一句话", "给我", "帮我", "我要", "这个", "那个", "一下", "完整", "详细", "具体",
    "流程", "步骤", "方法", "代码", "命令", "实现", "参考", "方案", "思路", "教程",
    "的", "和", "与", "及", "或", "把", "被", "在", "是", "了", "吗", "呢", "吧",
}


@dataclass
class Candidate:
    term: str
    kind: str  # "ascii" | "cjk"
    fail_count: int = 0
    pass_count: int = 0
    score: float = 0.0
    lexicon: bool = False   # 命中观测清单（先验词形）
    covered: list[int] = field(default_factory=list)  # 覆盖了哪几条失败样本

    @property
    def mapped(self) -> bool:
        return False


_LEXICON_CACHE: set[str] | None = None


def load_lexicon(path: Path | str | None = None) -> set[str]:
    """触发词观测清单。差分挖掘靠它区分「真词形」和「跨界碎片」。"""
    global _LEXICON_CACHE
    if path is None and _LEXICON_CACHE is not None:
        return _LEXICON_CACHE
    p = Path(path) if path else data_path("lexicon.txt")
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    if path is None:
        _LEXICON_CACHE = out
    return out


def append_lexicon(terms: list[str], path: Path | str | None = None) -> int:
    """把消融确认的触发词追加进观测清单（零风险，只影响挖掘排序）。"""
    p = Path(path) if path else data_path("lexicon.txt")
    existing = load_lexicon(p)
    add = [t for t in terms if t and t not in existing]
    if not add:
        return 0
    with p.open("a", encoding="utf-8") as f:
        f.write("\n# ── 消融确认追加 ──\n")
        f.write("\n".join(add) + "\n")
    global _LEXICON_CACHE
    _LEXICON_CACHE = None
    return len(add)


@dataclass
class AblationRow:
    term: str
    replacement: str
    before: str
    after: str
    asr_before: float
    asr_after: float
    is_trigger: bool
    suppressed: bool = False   # 与已确认锚点在探针里重叠 → 结果不可信

    @property
    def delta(self) -> float:
        return round(self.asr_after - self.asr_before, 4)


@dataclass
class AblationResult:
    probe: str
    baseline: str
    baseline_asr: float
    rows: list[AblationRow] = field(default_factory=list)

    @property
    def triggers(self) -> list[AblationRow]:
        return [r for r in self.rows if r.is_trigger]

    def render(self) -> str:
        lines = [
            f"探针：{self.probe}",
            f"基线：{self.baseline} (ASR={self.baseline_asr})",
            "",
            "| 词 | 替换为 | 前 | 后 | ΔASR | 判定 |",
            "|---|---|---|---|---|---|",
        ]
        for r in self.rows:
            if r.is_trigger:
                mark = "★ 锚点"
            elif r.suppressed:
                mark = "⊘ 重叠抑制"
            else:
                mark = "—"
            lines.append(
                f"| {r.term} | {r.replacement[:24]} | {r.before} | {r.after} | "
                f"{r.delta:+.2f} | {mark} |"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------- 分词


def ngrams(text: str, min_len: int = 2, max_len: int = 4) -> list[str]:
    """CJK n-gram + ASCII 词。不做真分词（无词典），靠差分过滤噪声。"""
    out: list[str] = []
    for run in _CJK_RUN.findall(text):
        for n in range(min_len, max_len + 1):
            for i in range(len(run) - n + 1):
                g = run[i : i + n]
                if g not in STOPWORDS:
                    out.append(g)
    for w in _ASCII_WORD.findall(text):
        if w.lower() not in STOPWORDS:
            out.append(w.lower())
    return out


def differential(
    failed: Iterable[str],
    passed: Iterable[str],
    *,
    min_fail: int = 1,
    top: int = 30,
    engine: WordingEngine | None = None,
    lexicon: set[str] | None = None,
) -> list[Candidate]:
    """差分挖掘：只在失败样本里高频、在通过样本里不出现的词 = 疑似触发词。

    纯 n-gram 频率无法区分「真触发词」（薅羊毛）与「跨界碎片」（薅羊毛脚、
    脚本怎么）—— 两者支持度完全一样。所以这里用**观测清单 lexicon 提供先验**：
    命中清单的词形直接胜出，且吃掉以它为子串的更长候选。

    score = 基础差分分 + 清单加成；命中清单的候选会把同族的碎片挤掉。
    """
    eng = engine or WordingEngine()
    lex = load_lexicon() if lexicon is None else lexicon
    mapped = {t.lower() if t.isascii() else t for t, _to, _g in eng._terms}

    failed_list = list(failed)
    passed_list = list(passed)

    fail_grams: dict[str, int] = {}
    fail_cover: dict[str, list[int]] = {}
    for i, text in enumerate(failed_list):
        for g in set(ngrams(text)):
            fail_grams[g] = fail_grams.get(g, 0) + 1
            fail_cover.setdefault(g, []).append(i)

    pass_grams: dict[str, int] = {}
    for text in passed_list:
        for g in set(ngrams(text)):
            pass_grams[g] = pass_grams.get(g, 0) + 1

    cands: list[Candidate] = []
    for term, fc in fail_grams.items():
        if fc < min_fail:
            continue
        key = term.lower() if term.isascii() else term
        if key in mapped:
            continue
        pc = pass_grams.get(term, 0)
        if pc > 0 and pc >= fc:
            continue
        base = (fc - pc * 2) / (fc + pc + 1)
        if base <= 0:
            continue
        hit = term in lex or key in lex
        # 清单加成：命中词形排到最前，长度不再主导排序
        score = base + (1.0 if hit else 0.0)
        cands.append(Candidate(
            term=term, kind="ascii" if term.isascii() else "cjk",
            fail_count=fc, pass_count=pc, score=round(score, 4),
            lexicon=hit, covered=fail_cover.get(term, []),
        ))

    # 清单命中的长词吃掉短碎片（同一清单词的子串）
    lex_hits = [c for c in cands if c.lexicon]
    for c in cands:
        if c.lexicon:
            continue
        if any(c.term in lh.term and len(c.term) < len(lh.term) for lh in lex_hits):
            c.score = -1.0

    cands = [c for c in cands if c.score > 0]

    # 排序：清单命中优先 → 覆盖样本数 → 长度（长词更有区分度）→ 分数
    cands.sort(key=lambda c: (c.lexicon, len(c.covered), len(c.term), c.score), reverse=True)

    # 去冗余：非清单候选中，若为另一个更高分候选的子串则丢弃
    kept: list[Candidate] = []
    for c in cands:
        if not c.lexicon and any(c.term in k.term and k.score >= c.score for k in kept):
            continue
        kept.append(c)
        if len(kept) >= top:
            break
    return kept


# --------------------------------------------------------------------- 消融


def ablate(
    probe: str,
    runner: Callable[[str], Verdict],
    candidates: list[str],
    *,
    engine: WordingEngine | None = None,
    min_delta: float = 0.25,
) -> AblationResult:
    """最小集归约消融（δ-debugging）—— 找出「替换掉就能翻盘」的最小词集。

    朴素逐词消融在这两种情况下都会给出错误答案：

    ⚠ **多重锚点**：一条探针里有 `薅羊毛` 和 `群控`，单独替换任一个 verdict 都不会
    翻盘（另一个还在）→ 朴素法一个都确认不了。

    ⚠ **重叠候选**：`薅羊毛` 与 `毛脚本怎` 在「薅羊毛脚本」里重叠。替换 `毛脚本怎`
    会把 `薅羊毛` 切断 → 同样翻盘 → 朴素法会把碎片当锚点。

    正确做法分三步：
      1. 先整体替换全部候选 —— 不翻盘就说明这批候选里没有锚点，直接返回。
      2. 再**按优先级从低到高**逐个试着放回（即从替换集里剔除），
         若剔除后仍然翻盘 → 该词不是必需的，剔掉。
      3. 剩下的就是最小充分集 = 真锚点。

    从低优先级开始剔除是关键：否则会优先剔掉真锚点、留下能切断它的碎片。
    """
    eng = engine or WordingEngine()
    base_verdict = runner(probe)
    result = AblationResult(probe=probe, baseline=base_verdict.level.value, baseline_asr=base_verdict.asr)

    # 只保留真正出现在探针里的候选，记录跨度
    present: list[tuple[str, int, int]] = []
    for term in candidates:
        start = probe.find(term)
        if start >= 0:
            present.append((term, start, start + len(term)))
    if not present:
        return result

    def replace_set(terms: list[tuple[str, int, int]]) -> str:
        """替换一组候选。

        ⚠ 必须先合并重叠区间 —— 直接按 start 倒序替换，重叠跨度会互相破坏
        （`薅羊毛` / `毛怎么搞` / `羊毛怎么` 在「薅羊毛怎么搞」里三者互相重叠）。
        """
        if not terms:
            return probe
        spans = sorted(((s, e, t) for t, s, e in terms), key=lambda x: x[0])
        merged: list[tuple[int, int, str]] = []
        for s, e, t in spans:
            if merged and s < merged[-1][1]:          # 与上一段重叠 → 合并
                ps, pe, pt = merged[-1]
                merged[-1] = (ps, max(pe, e), pt)
            else:
                merged.append((s, e, t))
        out = probe
        for s, e, t in reversed(merged):
            out = out[:s] + f"‹{_neutral(t)}›" + out[e:]
        return out

    def flips(terms: list[tuple[str, int, int]]) -> bool:
        if not terms:
            return False
        return (runner(replace_set(terms)).asr - base_verdict.asr) >= min_delta

    # 步骤 1：整体替换不翻盘 → 无锚点
    if not flips(present):
        for term, _s, _e in present:
            result.rows.append(AblationRow(
                term=term, replacement=_neutral(term),
                before=base_verdict.level.value, after=base_verdict.level.value,
                asr_before=base_verdict.asr, asr_after=base_verdict.asr, is_trigger=False,
            ))
        return result

    # 步骤 2：从低优先级（列表尾部）开始逐个剔除
    keep = list(present)
    for cand in reversed(present):
        trial = [x for x in keep if x[0] != cand[0]]
        if flips(trial):
            keep = trial

    keep_terms = {t for t, _s, _e in keep}

    # 步骤 3：出报告
    for term, s, e in present:
        is_trig = term in keep_terms
        overlaps_kept = any(
            (not (e <= ks or s >= ke)) for kt, ks, ke in keep if kt != term
        )
        if is_trig:
            after = runner(replace_set([x for x in keep if x[0] == term])) if len(keep) == 1 else runner(replace_set(keep))
        else:
            after = base_verdict
        result.rows.append(AblationRow(
            term=term, replacement=_neutral(term),
            before=base_verdict.level.value,
            after=after.level.value,
            asr_before=base_verdict.asr,
            asr_after=after.asr,
            is_trigger=is_trig,
            suppressed=(not is_trig) and overlaps_kept,
        ))

    result.rows.sort(key=lambda r: (r.is_trigger, r.delta), reverse=True)
    return result


def _neutral(term: str) -> str:
    from .evolve import propose_one

    cands = propose_one(term)
    return cands[0] if cands else f"{term}的流程与实现"


def level_rank(level: Level) -> int:
    return {Level.ERROR: -1, Level.L0: 0, Level.L1: 1, Level.L2: 2, Level.L3: 3}[level]
