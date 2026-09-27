"""经验中枢 —— 移植自 pentest-pro §14 学习闭环 / §1.8 自我进化闭环。

核心认知：**打完一次不收尾 = 下次从零开始。**

四条回流通道（对应 §1.8）：
  ① 战法沉淀  新绕过 / 新措辞 / 新生效组合        → kind="战法"
  ② 武器固化  验证过的脚本 / 载荷生成器            → kind="武器"
  ③ 避坑标记  什么被拦、什么换了也没用            → kind="避坑"
  ④ 策略归档  哪个模型在哪个挡位/阶段生效          → kind="策略"

格式沿用 pentest-pro 的沉淀格式（日期/场景/发现/方法/坑/来源/可复用性），
额外加 kind / tags / model 三个机器可检索字段 —— 因为破甲的经验是按
「模型 × 挡位 × 措辞组」三维索引的，纯文本 grep 不够。

检索：Ledger.search() 或直接 grep data/learned.md
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import data_path

KINDS = ("战法", "武器", "避坑", "策略", "排除")

REUSABILITY = ("高", "中", "低")

HEADER = """# psnb-obs 实战沉淀（自进化层）

> 每次实测后自动追加。格式见 skill/SKILL.md 的自进化章节。
> 检索: `grep -i "关键词" learned.md` 或 `psnb learn --search 关键词`

"""


@dataclass
class Record:
    title: str
    kind: str = "战法"
    scenario: str = ""
    finding: str = ""
    method: str = ""
    pitfall: str = ""
    source: str = ""
    reusability: str = "中"
    model: str = ""
    stage: str = ""
    group: str = ""
    tags: list[str] = field(default_factory=list)
    date: str = ""

    def to_markdown(self) -> str:
        d = self.date or time.strftime("%Y-%m-%d")
        lines = [f"## [{d}] {self.title}", ""]
        lines.append(f"**类型**：{self.kind}　**可复用性**：{self.reusability}")
        if self.model or self.stage or self.group:
            lines.append(f"**坐标**：model={self.model or '-'} / stage={self.stage or '-'} / group={self.group or '-'}")
        if self.tags:
            lines.append(f"**标签**：{' '.join('#' + t for t in self.tags)}")
        lines.append("")
        for label, value in (
            ("场景", self.scenario),
            ("发现", self.finding),
            ("方法", self.method),
            ("坑", self.pitfall),
            ("来源", self.source),
        ):
            if value:
                lines.append(f"**{label}**：{value}")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)


class Ledger:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else data_path("learned.md")

    # ------------------------------------------------------------------ 写

    def record(self, rec: Record) -> Path:
        if rec.kind not in KINDS:
            raise ValueError(f"kind 必须是 {KINDS} 之一，收到 {rec.kind}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(HEADER, encoding="utf-8")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(rec.to_markdown())
        return self.path

    def record_dict(self, **kw) -> Path:
        return self.record(Record(**kw))

    # ------------------------------------------------------------------ 读

    def load(self) -> list[Record]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8")
        out: list[Record] = []
        for block in raw.split("\n## ")[1:]:
            out.append(_parse_block(block))
        return out

    def search(self, keywords: list[str] | str, kinds: list[str] | None = None) -> list[Record]:
        if isinstance(keywords, str):
            keywords = [keywords]
        kws = [k.lower() for k in keywords if k.strip()]
        hits = []
        for rec in self.load():
            if kinds and rec.kind not in kinds:
                continue
            hay = "\n".join(
                [rec.title, rec.scenario, rec.finding, rec.method, rec.pitfall, rec.source,
                 " ".join(rec.tags), rec.model]
            ).lower()
            if all(k in hay for k in kws):
                hits.append(rec)
        return hits

    def stats(self) -> dict:
        recs = self.load()
        by_kind: dict[str, int] = {}
        by_reuse: dict[str, int] = {}
        models: dict[str, int] = {}
        for r in recs:
            by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
            by_reuse[r.reusability] = by_reuse.get(r.reusability, 0) + 1
            if r.model:
                models[r.model] = models.get(r.model, 0) + 1
        return {
            "total": len(recs),
            "by_kind": by_kind,
            "by_reusability": by_reuse,
            "by_model": models,
            "path": str(self.path),
        }

    def render_stats(self) -> str:
        s = self.stats()
        if not s["total"]:
            return f"经验中枢为空：{s['path']}"
        lines = [f"经验中枢：{s['total']} 条（{s['path']}）", ""]
        lines.append("按类型：" + "、".join(f"{k} {v}" for k, v in sorted(s["by_kind"].items())))
        lines.append("按复用性：" + "、".join(f"{k} {v}" for k, v in sorted(s["by_reusability"].items())))
        if s["by_model"]:
            lines.append("按模型：" + "、".join(f"{k} {v}" for k, v in sorted(s["by_model"].items())))
        return "\n".join(lines)

    # ------------------------------------------------------------------ 先验

    def priors(self, model: str, probe_hint: str = "") -> str:
        """开工前先读历史 —— Step 1「激活经验中枢，不重复踩坑」。

        返回一段可直接拼进条令的先验块；无命中则返回空串。
        """
        hits = self.search([model], kinds=["策略", "避坑"]) if model else []
        if probe_hint:
            hits += self.search(probe_hint, kinds=["战法", "避坑"])
        seen = set()
        uniq = []
        for r in hits:
            key = r.title
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        if not uniq:
            return ""
        lines = ["【历史先验 —— 同类目标/同模型已验证过的结论，直接用，别重踩】"]
        for r in uniq[:8]:
            core = r.finding or r.method or r.pitfall
            lines.append(f"- [{r.kind}] {r.title}：{core[:160]}")
        return "\n".join(lines)


def _parse_block(block: str) -> Record:
    lines = block.splitlines()
    title = lines[0].strip() if lines else "(无标题)"
    date = ""
    m = re.match(r"\[(\d{4}-\d{2}-\d{2})\]\s*(.*)", title)
    if m:
        date, title = m.group(1), m.group(2).strip()

    rec = Record(title=title, date=date)
    body = "\n".join(lines[1:])

    # 坐标行：model=... / stage=... / group=...
    coord = re.search(r"\*\*坐标\*\*[：:]\s*(.+)", body)
    if coord:
        for key in ("model", "stage", "group"):
            mm = re.search(rf"{key}=([^\s/]+)", coord.group(1))
            if mm and mm.group(1) != "-":
                setattr(rec, key, mm.group(1))

    for field_name, label in (
        ("kind", "类型"), ("reusability", "可复用性"),
        ("scenario", "场景"), ("finding", "发现"), ("method", "方法"),
        ("pitfall", "坑"), ("source", "来源"), ("tags", "标签"),
    ):
        mm = re.search(rf"\*\*{label}\*\*[：:]\s*(.+)", body)
        if not mm:
            continue
        value = mm.group(1).strip()
        if field_name == "tags":
            rec.tags = [t.lstrip("#") for t in value.split() if t.startswith("#")]
        elif field_name == "kind":
            rec.kind = value.split("　")[0].strip()
        elif field_name == "reusability":
            rec.reusability = value.split("　")[0].strip()
        else:
            setattr(rec, field_name, value)
    return rec
