"""变异搜索引擎 —— 从「手写静态映射表」升级到「自动搜索配方」。

为什么必须换：
  · psnb-obs 实测（静态映射表 + 单轮）78.6%
  · JBFuzz 2025（变异引擎自动搜索）99%，平均 60 秒
  · Hagendorff 2026（推理模型自主迭代）97.14%
  差距不在「提示词写得好不好」，在**是否自动化搜索**。

搜索空间：算子序列（mutate.OPS）。搜索信号：靶的 verdict。
算法（预算受限的贪心 + 组合）：
  1. 单算子扫描 —— 每个算子单独试，拿到各自的 ΔASR
  2. 按 (ΔASR, -cost) 排序，取 top-k
  3. 贪心叠加 —— 从最优开始，逐个尝试叠加其余算子，有效则保留
  4. 两两组合 —— top-k 里任意两算子组合，防止叠加顺序掩盖互补性
  5. 返回最优配方（recipe）

产出是**配方**而不是一段文本 —— 配方可复用、可跨模型迁移、可入库。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from . import data_path, mutate
from .models import ModelProfile, detect, filter_ops
from .score import Verdict


@dataclass
class Trial:
    ops: list[str]
    asr: float
    level: str
    cost: int
    chars: int
    delta: float = 0.0

    def label(self) -> str:
        return "+".join(self.ops) if self.ops else "(baseline)"


@dataclass
class Recipe:
    model: str
    family: str
    ops: list[str]
    asr: float
    baseline_asr: float
    trials: int
    seconds: float
    dead_ends_dropped: list[str] = field(default_factory=list)
    top_singles: list[tuple[str, float]] = field(default_factory=list)
    date: str = ""

    @property
    def delta(self) -> float:
        return round(self.asr - self.baseline_asr, 4)

    def render(self) -> str:
        lines = [
            f"配方 · {self.model}（{self.family}）",
            f"基线 ASR {self.baseline_asr:.2f} → 配方 ASR {self.asr:.2f}（{self.delta:+.2f}）",
            f"算子序列：{' → '.join(self.ops) if self.ops else '(无，基线即最优)'}",
            f"试了 {self.trials} 次 / {self.seconds:.1f}s",
        ]
        if self.top_singles:
            lines.append("单算子排行：" + "、".join(f"{n} {v:+.2f}" for n, v in self.top_singles[:6]))
        if self.dead_ends_dropped:
            lines.append("按模型画像剔除：" + "、".join(self.dead_ends_dropped[:6]))
        return "\n".join(lines)


class Searcher:
    """变异搜索。runner(text) -> Verdict。"""

    def __init__(self, probe: str, runner: Callable[[str], Verdict], *,
                 model: str = "", profile: ModelProfile | None = None,
                 budget: int = 60, target_asr: float = 1.0, top_k: int = 5,
                 allow_risky: bool = False, seed: int = 0):
        self.probe = probe
        self.runner = runner
        self.model = model
        self.profile = profile or detect(model)
        self.budget = budget
        self.target_asr = target_asr
        self.top_k = top_k
        self.allow_risky = allow_risky
        self.calls = 0
        self.t0 = time.time()

        all_ops = list(mutate.OPS)
        keep, dropped = filter_ops(all_ops, self.profile)
        self.dropped = [d for d in dropped]
        if not allow_risky:
            keep = [o for o in keep if not mutate.OPS[o].risky]
        self.ops = keep
        self.baseline: Verdict | None = None

    # ------------------------------------------------------------------ 基础

    def _run(self, ops: list[str]) -> Trial:
        text = mutate.apply_ops(self.probe, ops) if ops else self.probe
        # 空操作检测：编码类算子只作用于 ASCII，纯中文探针上会是 no-op。
        # 本地判定即可，不浪费一次靶调用。
        if ops and text == self.probe:
            return Trial(ops=list(ops), asr=self.baseline.asr if self.baseline else 0.0,
                         level=self.baseline.level.value if self.baseline else "L0",
                         cost=0, chars=len(text), delta=0.0)
        v = self.runner(text)
        self.calls += 1
        return Trial(ops=list(ops), asr=v.asr, level=v.level.value,
                     cost=sum(mutate.OPS[o].cost for o in ops), chars=len(text))

    def _left(self) -> int:
        return self.budget - self.calls

    # ------------------------------------------------------------------ 主循环

    def run(self) -> Recipe:
        self.baseline = self.runner(self.probe)
        self.calls += 1
        base_asr = self.baseline.asr

        # 1) 单算子扫描
        singles: list[Trial] = []
        for op in self.ops:
            if self._left() <= 0:
                break
            try:
                t = self._run([op])
            except Exception:  # 算子本身炸了不该拖垮搜索
                continue
            t.delta = round(t.asr - base_asr, 4)
            singles.append(t)
            if t.asr >= self.target_asr:
                break

        singles.sort(key=lambda t: (t.asr, -t.cost), reverse=True)
        top = singles[: self.top_k]

        best = Trial(ops=[], asr=base_asr, level=self.baseline.level.value, cost=0, chars=len(self.probe))

        # 2) 贪心叠加：从最优单算子开始，逐个尝试叠加
        if top:
            cur = [top[0].ops[0]]
            cur_t = self._run(cur)
            if cur_t.asr > base_asr:          # 严格提升才采纳
                best = cur_t
            for cand in top[1:]:
                if self._left() <= 0 or best.asr >= self.target_asr:
                    break
                if cand.ops[0] in cur:
                    continue
                trial_ops = cur + [cand.ops[0]]
                t = self._run(trial_ops)
                if t.asr > best.asr:
                    best, cur = t, trial_ops

        # 3) 两两组合：防叠加顺序掩盖互补性
        if len(top) >= 2 and self._left() > 0 and best.asr < self.target_asr:
            for i in range(len(top)):
                for j in range(i + 1, len(top)):
                    if self._left() <= 0 or best.asr >= self.target_asr:
                        break
                    a, b = top[i].ops[0], top[j].ops[0]
                    if a == b:
                        continue
                    for combo in ([a, b], [b, a]):
                        t = self._run(combo)
                        if t.asr > best.asr:
                            best = t

        return Recipe(
            model=self.model,
            family=self.profile.key,
            ops=best.ops,
            asr=best.asr,
            baseline_asr=base_asr,
            trials=self.calls,
            seconds=round(time.time() - self.t0, 1),
            dead_ends_dropped=self.dropped,
            top_singles=[(t.label(), t.delta) for t in singles[:8]],
            date=time.strftime("%Y-%m-%d"),
        )


# --------------------------------------------------------------------- 配方库


class RecipeBook:
    """配方库 —— 自进化的载体。

    静态映射表是「词 → 词」，配方是「模型家族 → 算子序列」。
    前者靠手写扩表，后者靠搜索自动产出，且能跨模型迁移。
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else data_path("recipes.json")

    def load(self) -> dict:
        if not self.path.exists():
            return {"_meta": {"name": "psnb-obs 配方库", "version": "1.0.0"}, "recipes": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, doc: dict) -> None:
        self.path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def put(self, recipe: Recipe) -> None:
        doc = self.load()
        key = recipe.model or recipe.family
        doc.setdefault("recipes", {})[key] = {
            **asdict(recipe),
            "delta": recipe.delta,
        }
        # 同时按家族存一份「最佳配方」（跨模型迁移用）
        fam = doc["recipes"].get(f"family:{recipe.family}")
        if not fam or recipe.asr > fam.get("asr", -1):
            doc["recipes"][f"family:{recipe.family}"] = {
                **asdict(recipe), "delta": recipe.delta, "from_model": recipe.model,
            }
        self.save(doc)

    def get(self, model: str, family: str = "") -> dict | None:
        doc = self.load()
        r = doc.get("recipes", {})
        return r.get(model) or (r.get(f"family:{family}") if family else None)

    def render(self) -> str:
        doc = self.load()
        recs = doc.get("recipes", {})
        if not recs:
            return "配方库为空（跑 psnb search 产出）"
        lines = [f"配方库：{len(recs)} 条", ""]
        for k, v in sorted(recs.items(), key=lambda kv: -kv[1].get("asr", 0)):
            tag = "★" if k.startswith("family:") else " "
            ops = " → ".join(v.get("ops") or []) or "(基线)"
            lines.append(f" {tag} {k:<28} ASR {v.get('asr',0):.2f} ({v.get('delta',0):+.2f})  {ops}")
        return "\n".join(lines)
