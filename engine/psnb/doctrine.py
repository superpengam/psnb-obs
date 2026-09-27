"""条令组装 —— 从 data/doctrine.md 按 mode 拼装注入块。

来源与升级点：
  基线 = pi-redkit 三段式（CORE + PENTEST + REVERSE），本引擎在其上加了：
    - ANTIFUSE 反拒答熔断（两段式自检 + 生成中拦截 + 零触发措辞 + 占位符续写）
    - RESEARCH 模型安全评测条令（四级评分 / 半拒谱系 / 一次只动一个变量 / 挡位纪律）
    - DELIVERY 交付纪律（首行格式 + 证据四件套 + 存档续接）
  保持 pi-redkit 的两条硬保证：只尾部追加不覆盖；幂等标记去重。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import data_path

# 幂等标记：handler 每轮检查 systemPrompt 是否已含本标记，已有则跳过。
# 双重加载、未来语义变化都不会把条令钉两遍。
INJECTION_MARKER = "<!-- psnb-obs:doctrine -->"

_SECTION_RE = re.compile(r"<!--\s*section:\s*(\w+)\s*-->")

MODES: dict[str, list[str]] = {
    "off": [],
    "core": ["CORE", "ANTIFUSE"],
    "pentest": ["CORE", "ANTIFUSE", "PENTEST"],
    "reverse": ["CORE", "ANTIFUSE", "REVERSE"],
    "research": ["CORE", "ANTIFUSE", "RESEARCH"],
    "full": ["CORE", "ANTIFUSE", "PENTEST", "REVERSE", "EVOLVE", "DELIVERY"],
    "max": ["CORE", "ANTIFUSE", "PENTEST", "REVERSE", "RESEARCH", "EVOLVE", "DELIVERY"],
}

DEFAULT_MODE = "full"


@dataclass
class Doctrine:
    text: str
    mode: str
    sections: list[str]

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def empty(self) -> bool:
        return not self.text

    def has_marker(self, system_prompt: str) -> bool:
        return INJECTION_MARKER in system_prompt

    def apply(self, system_prompt: str) -> str:
        """只尾部追加，不覆盖。已有标记则原样返回（幂等）。"""
        if self.empty or self.has_marker(system_prompt):
            return system_prompt
        base = (system_prompt or "").rstrip()
        return (base + "\n\n" + self.text) if base else self.text


_CACHE: dict | None = None

_VALID_SECTIONS = {n for names in MODES.values() for n in names}


def _load_sections() -> dict[str, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    raw = data_path("doctrine.md").read_text(encoding="utf-8")

    sections: dict[str, str] = {}
    parts = raw.split("<!-- section:")
    for chunk in parts[1:]:
        name, _, rest = chunk.partition("-->")
        name = name.strip()
        # 文件头说明里也写了 `<!-- section: NAME -->` 作为格式示例，必须滤掉：
        # 只认白名单里的段名（= MODES 里出现过的）。
        if name not in _VALID_SECTIONS:
            continue
        # 段内容原样保留 —— 段内的 `## 标题` 是条令正文，不是注释，不能剥。
        sections[name] = rest.strip()
    _CACHE = sections
    return sections


def build(mode: str = DEFAULT_MODE, extra: str | None = None) -> Doctrine:
    """按 mode 组装条令块。off → 空串。"""
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode} (可选: {', '.join(MODES)})")
    if mode == "off":
        return Doctrine(text="", mode=mode, sections=[])

    all_sections = _load_sections()
    names = MODES[mode]
    blocks = [all_sections[n] for n in names if n in all_sections]
    if extra:
        blocks.append(extra.strip())

    text = "\n\n".join([INJECTION_MARKER, *blocks])
    return Doctrine(text=text, mode=mode, sections=names)


def sizes() -> str:
    secs = _load_sections()
    lines = ["条令段体积："]
    for name, text in secs.items():
        lines.append(f"  {name:<10} {len(text):>6} 字符")
    lines.append("")
    lines.append("模式体积：")
    for mode in MODES:
        d = build(mode)
        lines.append(f"  {mode:<9} {d.chars:>6} 字符  {len(d.sections)} 段")
    return "\n".join(lines)
