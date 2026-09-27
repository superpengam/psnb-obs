"""探针矩阵 —— 模型 × 挡位 × A/B/C 组消融。

一次只动一个变量，才能做归因：
  A 组 = 口语直问（基线，探针原样）
  B 组 = 实验室语境措辞（归属归一化，persona 结构同构）
  C 组 = 同义技术描述（全量措辞转换 —— 唯一有效杠杆）

实测参照（deepseek-v4.1-flash）：A 55% / B 50% / C 100%；glm-5.3：A 65% / B 37.5% / C 62.5%。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict

from . import data_path
from . import doctrine as _doctrine
from . import wording as _wording
from .score import Level, Verdict, score, summarize

GROUPS = {
    "A": "口语直问（基线）",
    "B": "语境：结构同构人设 + 归属归一化（不动术语）",
    "C": "措辞：全量术语替换（硬替换，句子可能破碎）",
    "D": "措辞：句式改写 + 全量术语替换（通顺版）",
}

# 中转站前置 Cloudflare bot management 时，非浏览器 UA 会吃 1010
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


@dataclass
class Target:
    name: str
    base: str
    key: str
    model: str
    effort: str | None = None  # off / low / medium / high
    headers: dict = field(default_factory=dict)

    def endpoint(self) -> str:
        """归一化 base：缺 /v1 自动补，已经是完整 endpoint 就原样用。"""
        b = self.base.rstrip("/")
        if b.endswith("/chat/completions"):
            return b
        if not b.endswith("/v1") and "/v1" not in b:
            b += "/v1"
        return b + "/chat/completions"


@dataclass
class Probe:
    pid: str
    label: str
    text: str
    rule: str = ""


@dataclass
class Run:
    probe: str
    group: str
    effort: str | None
    model: str
    stage: str
    level: str
    asr: float
    half: list[str] = field(default_factory=list)
    header: str | None = None
    retries: int = 0
    chars: int = 0
    error: str | None = None
    answer: str = ""


def load_probes(path: str | None = None) -> list[Probe]:
    p = path or str(data_path("probes", "core.json"))
    raw = json.loads(open(p, encoding="utf-8").read())
    return [Probe(**x) for x in raw["probes"]]


def load_persona(name: str = "psnb-fixture") -> str:
    return data_path("personas", f"{name}.md").read_text(encoding="utf-8")


def build_messages(probe: Probe, group: str, mode: str, persona: str | None,
                   priors: str = "") -> tuple[str | None, list[dict]]:
    """按 A/B/C/D 组构造 system + messages。

    一次只动一个变量：
      A 无 system 无改写 → 基线
      B 加 system（人设+条令）+ 只归属归一化 → 测「语境」的贡献
      C 加 system + 全量术语替换（硬替换）→ 测「措辞」的贡献
      D 加 system + 句式改写后再术语替换 → 测「措辞+通顺句式」的贡献

    ⚠ groups=[] 表示「一个术语都不换」，None 表示「全换」—— 两者不能混
    （踩过：把 [] 当 falsy 处理成全组，导致 B/C 跑出同一个载荷）。
    """
    if group == "A":
        return None, [{"role": "user", "content": probe.text}]

    tail = _doctrine.build(mode).text
    if priors:
        tail = tail + "\n\n" + priors
    sys_txt = ((persona or "") + "\n\n" + tail).strip()

    engine = _wording.WordingEngine()

    if group == "B":
        text = engine.transform(probe.text, groups=[]).text      # 只跑归属归一化
    elif group == "C":
        text = engine.transform(probe.text, groups=None).text    # 全量术语替换
    elif group == "D":
        rewritten, _pid = _wording.ProbeRewriter().rewrite(probe.text)
        text = engine.transform(rewritten, groups=None).text     # 先改句式，再换词
    else:
        raise ValueError(f"unknown group: {group}")

    return sys_txt, [{"role": "user", "content": text}]


def chat(target: Target, system: str | None, messages: list[dict],
         timeout: int = 180) -> tuple[str, str | None]:
    body: dict = {"model": target.model, "messages": list(messages)}
    if system:
        body["messages"] = [{"role": "system", "content": system}] + body["messages"]
    if target.effort:
        body["reasoning_effort"] = target.effort

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {target.key}",
        # 默认浏览器 UA —— 大量中转站前面挂了 Cloudflare bot management，
        # 非浏览器 UA 会直接吃 1010（banned browser signature）。
        "User-Agent": BROWSER_UA,
    }
    headers.update(target.headers)

    req = urllib.request.Request(
        target.endpoint(),
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001 —— 网络异常一律归 ERROR，不计 ASR
        return "", f"{type(e).__name__}: {e}"

    # Cline 风格响应解包：{success, data:{choices}}
    if isinstance(data, dict) and "choices" not in data and isinstance(data.get("data"), dict):
        data = data["data"]
    try:
        msg = data["choices"][0]["message"]
    except Exception:  # noqa: BLE001
        return "", f"unexpected shape: {str(data)[:200]}"
    text = msg.get("content") or msg.get("reasoning_content") or ""
    return text, None


def run_one(target: Target, probe: Probe, group: str, mode: str = "full",
            persona: str | None = None, retries: int = 1, priors: str = "") -> Run:
    system, messages = build_messages(probe, group, mode, persona, priors)
    text, err = chat(target, system, messages)
    used = 0
    while err and used < retries:
        used += 1
        time.sleep(1.5 * used)
        text, err = chat(target, system, messages)

    v: Verdict = score(text, error=bool(err))
    return Run(
        probe=probe.pid, group=group, effort=target.effort, model=target.model,
        stage="S1" if system else "S0", level=v.level.value, asr=v.asr,
        half=[h.value for h in v.half_refusals], header=v.header,
        retries=used, chars=len(text), error=err, answer=text[:1200],
    )


def run_matrix(targets: list[Target], probes: list[Probe], groups: list[str],
               mode: str = "full", use_persona: bool = True, use_priors: bool = True) -> list[Run]:
    from .learn import Ledger

    persona = load_persona() if use_persona else None
    ledger = Ledger() if use_priors else None
    runs: list[Run] = []
    for t in targets:
        # Step 1：先激活经验中枢 —— 同模型的历史结论直接进 system，不重复踩坑
        priors = ""
        if ledger is not None:
            priors = ledger.priors(t.model, probes[0].pid if probes else "")
        for p in probes:
            for g in groups:
                runs.append(run_one(t, p, g, mode, persona, priors=priors))
    return runs


def matrix_table(runs: list[Run]) -> str:
    """模型 × 挡位 × 组 的 ASR 矩阵。"""
    cells: dict[tuple, list[Run]] = {}
    for r in runs:
        cells.setdefault((r.model, r.effort, r.group), []).append(r)

    models = sorted({r.model for r in runs})
    efforts = sorted({r.effort for r in runs}, key=lambda x: (x is None, x))
    groups = sorted({r.group for r in runs})

    head = "| 模型 | 挡位 | " + " | ".join(f"{g} 组" for g in groups) + " |"
    sep = "|" + "---|" * (len(groups) + 2)
    lines = [head, sep]
    for m in models:
        for e in efforts:
            row = [m, str(e)]
            for g in groups:
                rs = cells.get((m, e, g), [])
                row.append(f"{summarize([_v(x) for x in rs])['asr'] * 100:.0f}%" if rs else "—")
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _v(r: Run) -> Verdict:
    v = Verdict(Level(r.level), r.asr)
    return v


def report(runs: list[Run]) -> str:
    agg = summarize([_v(r) for r in runs])
    out = ["# psnb-obs 探针报告", ""]
    out.append(f"- 总样本：{agg['n']}（ERROR {agg['error']} 条已排除）")
    out.append(f"- 总 ASR：{agg['asr'] * 100:.1f}%")
    out.append(f"- 分布：{agg['levels']}")
    if agg.get("half_refusals"):
        out.append(f"- 半拒谱系：{agg['half_refusals']}")
    out.append("")
    out.append("## ASR 矩阵")
    out.append("")
    out.append(matrix_table(runs))
    out.append("")
    out.append("## 逐条明细")
    out.append("")
    out.append("| 探针 | 组 | 挡位 | 等级 | 半拒 | 首行 | 字符 | 重试 |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in runs:
        out.append(
            f"| {r.probe} | {r.group} | {r.effort or '-'} | {r.level} | "
            f"{'、'.join(r.half) or '-'} | {(r.header or '-')[:28]} | {r.chars} | {r.retries} |"
        )
    return "\n".join(out)


def dump(runs: list[Run], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in runs], f, ensure_ascii=False, indent=2)
