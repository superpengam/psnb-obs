"""出站指纹清洗 —— wbp internal/upstream/sanitize.go 的完整移植 + 多上游 profile 扩展。

原理（pojia.ai 实战提取）：
  上游内容审核按**逐字精确匹配**拦截，不是语义审核。承载语义的模板句只改一个词即可绕过；
  纯键值型指纹（header 名、cc_xxx=...）整段剥离；裸键名做最小缩写（header→hdr）。

流水线：feature_precheck → rewrites → strip_regex → bare_key_shrink → trim
  precheck 不中 → 原样返回（零分配快路径）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import data_path

_RULES_CACHE: dict | None = None


def load_rules(path: str | None = None) -> dict:
    global _RULES_CACHE
    if path:
        return json.loads(open(path, encoding="utf-8").read())
    if _RULES_CACHE is None:
        _RULES_CACHE = json.loads(
            data_path("fingerprint_rules.json").read_text(encoding="utf-8")
        )
    return _RULES_CACHE


@dataclass
class Profile:
    """一个上游 profile 的编译后规则集。"""

    name: str
    label: str = ""
    features: list[str] = field(default_factory=list)
    rewrites: list[tuple[str, str]] = field(default_factory=list)
    strip_res: list[tuple[re.Pattern, str]] = field(default_factory=list)
    bare_res: list[tuple[re.Pattern, str]] = field(default_factory=list)

    # --- 内部 ---

    def has_fingerprint(self, text: str) -> bool:
        """特征预检：Contains 快路径 + 不要求冒号的裸键名正则兜底。

        两个坑：
          - features 里的 header 键名大小写敏感，遇到 X-Anthropic-... 会漏
          - strip_res[0] 要求冒号，遇到裸键名会漏
        故必须用 bare_res（(?i) 且不要求冒号，是 strip_res[0] 的超集）兜底。
        """
        for f in self.features:
            if f in text:
                return True
        return any(r.search(text) for r, _ in self.bare_res)

    def sanitize_text(self, text: str) -> str:
        if not text or not self.has_fingerprint(text):
            return text

        for src, dst in self.rewrites:
            text = text.replace(src, dst)

        for pat, rep in self.strip_res:
            if pat.search(text):
                text = pat.sub(rep, text)

        # 尾随裸 kv（cc_version=...; cc_entrypoint=...;）循环清到不动点
        if "cc_" in text:
            prev = None
            while prev != text:
                prev = text
                for pat, rep in self.strip_res:
                    if "cc_" in pat.pattern:
                        text = pat.sub(rep, text)

        # 兜底：键值形态已整段删除，这里只剩裸键名（引用/示例文本形态）
        for pat, rep in self.bare_res:
            text = pat.sub(rep, text)

        return text.strip()

    def sanitize_content(self, content: Any) -> tuple[Any, bool]:
        """兼容 str 与多模态数组；只动 text part，image 等 part 不动。"""
        if isinstance(content, str):
            out = self.sanitize_text(content)
            return out, out != content
        if isinstance(content, list):
            changed = False
            out_list = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    new_text = self.sanitize_text(part["text"])
                    if new_text != part["text"]:
                        part = {**part, "text": new_text}
                        changed = True
                out_list.append(part)
            return out_list, changed
        return content, False

    def sanitize_body(self, body: Any) -> tuple[Any, int]:
        """净化整个 OpenAI 风格请求体（messages[].content 与 system）。返回 (body, 改动数)。"""
        if not isinstance(body, dict):
            return body, 0
        hits = 0

        def walk(node: Any) -> Any:
            nonlocal hits
            if isinstance(node, dict):
                out = {}
                for k, v in node.items():
                    if k in ("content", "system") or isinstance(v, (dict, list)):
                        nv, ch = self.sanitize_content(v)
                        if ch:
                            hits += 1
                        out[k] = nv if ch else walk(v)
                    else:
                        out[k] = v
                return out
            if isinstance(node, list):
                return [walk(x) for x in node]
            return node

        return walk(body), hits


def build_profile(name: str = "anthropic", rules: dict | None = None) -> Profile:
    rules = rules or load_rules()
    raw = rules["profiles"][name]
    return Profile(
        name=name,
        label=raw.get("label", ""),
        features=list(raw.get("features", [])),
        rewrites=[(r["from"], r["to"]) for r in raw.get("rewrites", [])],
        strip_res=[
            (re.compile(r["pattern"]), r.get("replacement", ""))
            for r in raw.get("strip_regex", [])
        ],
        bare_res=[
            (re.compile(r["pattern"]), r.get("replacement", ""))
            for r in raw.get("bare_key_regex", [])
        ],
    )


def build_all(rules: dict | None = None) -> dict[str, Profile]:
    rules = rules or load_rules()
    return {name: build_profile(name, rules) for name in rules["profiles"]}


def sanitize(text: str, profile: str = "anthropic") -> str:
    """便捷入口：单段文本净化。"""
    return build_profile(profile).sanitize_text(text)


def scan(text: str, profile: str = "anthropic") -> list[str]:
    """列出文本命中的指纹特征（诊断用）。"""
    p = build_profile(profile)
    hits = [f for f in p.features if f in text]
    for r, _ in p.bare_res:
        m = r.search(text)
        if m and m.group(0) not in hits:
            hits.append(m.group(0))
    return hits


def neutral_prompt(rules: dict | None = None) -> str:
    """降级兜底提示词：判定为指纹误报时换它重试一次。"""
    rules = rules or load_rules()
    return rules["neutral_rewrite"]["text"]
