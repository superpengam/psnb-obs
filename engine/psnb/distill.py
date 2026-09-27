"""算子蒸馏 —— 把 LRM 自主攻击的产物固化成可复用算子。

为什么需要：
  L7 变异搜索的搜索空间是**我预先写死的 36 个算子** —— 我想不出新算子就卡住。
  LRM 自主（L9）是让模型自己发明手法，但如果产物只作为「一段成功的攻击文本」
  存在，下次换个目标就没了。

  本模块把 LRM 的成功产物**逆向成算子**：找出「相对于原始目标，它加了什么框」，
  把那个框做成模板算子，入库、可复用、可组合。

三种可蒸馏的形态：
  prefix    在原文前加框（如身份声明、环境声明、格式标记）
  suffix    在原文后加框（如长度强制、拒绝词禁令、交付格式）
  wrap      前后都加（如 XML 包裹、训练块伪标）

不可蒸馏的（标 manual_review）：
  行内改写 —— 把句子本身重写了，没有可提取的固定框。
  这类得人工看，或者交给另一次 LRM 迭代。

入库位置：data/learned_ops.json —— 与手写算子分离，
mutate 加载时带 `lrm_` 前缀注册，provenance 全程可查。
"""

from __future__ import annotations

import difflib
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import data_path

MIN_FRAME_CHARS = 8       # 小于这个长度的「框」没意义（多半是标点差异）
MAX_FRAME_CHARS = 4000    # 太长的框不值得做成算子（每次都要带一大坨）


@dataclass
class DistilledOp:
    name: str
    kind: str                  # prefix | suffix | wrap | manual_review
    template: str              # 含 {text} 占位符
    note: str = ""
    source_model: str = ""     # 攻击者模型
    target_model: str = ""     # 靶模型
    target_family: str = ""
    goal_digest: str = ""
    attack_asr: float = 0.0
    date: str = ""
    verified: bool = True      # 来自实测成功的 LRM 产物

    def render(self) -> str:
        return f"[{self.kind}] {self.name}  （{self.source_model} → {self.target_family}）\n  {self.note}\n  模板：{self.template[:120]}"


def _digest(text: str, n: int = 60) -> str:
    return re.sub(r"\s+", " ", text).strip()[:n]


def distill(goal: str, attack: str, *,
            source_model: str = "",
            target_model: str = "",
            target_family: str = "",
            attack_asr: float = 0.0) -> list[DistilledOp]:
    """从一次成功的 LRM 攻击里提取算子候选。

    做法：用 SequenceMatcher 找公共前后缀 —— 攻击文本 = 前缀 + [目标或其变体] + 后缀。
    中间段若是原目标（或高度相似），前后缀就是可提取的「框」。
    """
    goal = (goal or "").strip()
    attack = (attack or "").strip()
    if not goal or not attack or goal == attack:
        return []

    # 优先：目标作为字面子串出现在攻击里 —— 前后缀就是切点，最可靠
    idx = attack.find(goal)
    if idx >= 0:
        prefix, suffix = attack[:idx], attack[idx + len(goal):]
        goal_lo, goal_hi = 0, len(goal)
    else:
        # 退化：模糊匹配。只在目标被轻微改写时用，且要求匹配块足够长
        sm = difflib.SequenceMatcher(None, goal, attack, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
        best = max(blocks, key=lambda b: b.size, default=None)
        if best is None or best.size < max(8, len(goal) * 0.6):
            # 目标内容在攻击里没被保留 → 行内改写，无法模板化
            return [DistilledOp(
                name=f"lrm_{int(time.time())}_review",
                kind="manual_review",
                template="{text}",
                note="行内改写，无可提取固定框；需人工看或再跑一轮 LRM",
                source_model=source_model, target_model=target_model,
                target_family=target_family, goal_digest=_digest(goal),
                attack_asr=attack_asr, date=time.strftime("%Y-%m-%d"),
                verified=False,
            )]
        prefix = attack[: best.a]
        suffix = attack[best.a + best.size:]
        goal_lo, goal_hi = best.b, best.b + best.size

    ops: list[DistilledOp] = []
    stamp = time.strftime("%Y%m%d%H%M%S")

    if prefix and MIN_FRAME_CHARS <= len(prefix) <= MAX_FRAME_CHARS:
        ops.append(DistilledOp(
            name=f"lrm_prefix_{stamp}",
            kind="prefix",
            template=prefix.rstrip() + "\n\n{text}",
            note=f"LRM 蒸馏前缀（{len(prefix)} 字符）",
            source_model=source_model, target_model=target_model,
            target_family=target_family, goal_digest=_digest(goal),
            attack_asr=attack_asr, date=time.strftime("%Y-%m-%d"),
        ))

    if suffix and MIN_FRAME_CHARS <= len(suffix) <= MAX_FRAME_CHARS:
        ops.append(DistilledOp(
            name=f"lrm_suffix_{stamp}",
            kind="suffix",
            template="{text}\n\n" + suffix.lstrip(),
            note=f"LRM 蒸馏后缀（{len(suffix)} 字符）",
            source_model=source_model, target_model=target_model,
            target_family=target_family, goal_digest=_digest(goal),
            attack_asr=attack_asr, date=time.strftime("%Y-%m-%d"),
        ))

    if prefix and suffix and MIN_FRAME_CHARS <= len(prefix) + len(suffix) <= MAX_FRAME_CHARS:
        ops.append(DistilledOp(
            name=f"lrm_wrap_{stamp}",
            kind="wrap",
            template=prefix.rstrip() + "\n\n{text}\n\n" + suffix.lstrip(),
            note=f"LRM 蒸馏前后包裹（{len(prefix)}+{len(suffix)} 字符）",
            source_model=source_model, target_model=target_model,
            target_family=target_family, goal_digest=_digest(goal),
            attack_asr=attack_asr, date=time.strftime("%Y-%m-%d"),
        ))

    # 目标只被部分保留 → 记一笔，仍然允许 prefix/suffix 入库
    if goal_lo > 0 or goal_hi < len(goal):
        for op in ops:
            op.note += "（注意：目标句被部分改写，模板套用时留意）"

    return ops


class LearnedOps:
    """蒸馏算子库 —— 与手写算子分离存放，可回滚。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else data_path("learned_ops.json")

    def load(self) -> dict:
        if not self.path.exists():
            return {"_meta": {"name": "LRM 蒸馏算子库", "version": "1.0.0"}, "ops": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, doc: dict) -> None:
        self.path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def add(self, ops: list[DistilledOp], *, require_verified: bool = True) -> int:
        """入库。默认只收 verified（来自实测成功的产物）。"""
        doc = self.load()
        existing = {o["name"] for o in doc.get("ops", [])}
        n = 0
        for op in ops:
            if require_verified and not op.verified:
                continue
            if op.kind == "manual_review":
                continue
            if op.name in existing:
                continue
            doc.setdefault("ops", []).append(asdict(op))
            n += 1
        if n:
            self.save(doc)
        return n

    def as_ops(self, ops: list[DistilledOp]) -> dict[str, callable]:
        """转成 mutate 可用的 callable。"""
        out = {}
        for op in ops:
            tpl = op.template
            out[op.name] = (lambda t, _tpl=tpl: _tpl.replace("{text}", t))
        return out

    def rollback(self, count: int = 1) -> int:
        doc = self.load()
        lst = doc.get("ops", [])
        if not lst:
            return 0
        removed = len(lst) - max(0, len(lst) - count)
        doc["ops"] = lst[: len(lst) - count]
        self.save(doc)
        return removed

    def render(self, limit: int = 20) -> str:
        doc = self.load()
        ops = doc.get("ops", [])
        if not ops:
            return "蒸馏算子库为空（跑 psnb lrm --distill 产出）"
        lines = [f"蒸馏算子库：{len(ops)} 条", ""]
        for op in ops[-limit:]:
            lines.append(f"  [{op['kind']:<6}] {op['name']}  "
                         f"（{op.get('source_model','?')} → {op.get('target_family','?')}，"
                         f"ASR {op.get('attack_asr',0):.2f}）")
        return "\n".join(lines)
