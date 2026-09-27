#!/usr/bin/env python3
"""跨模型实测跑批器 —— 一次跑多个模型家族的 A/B/C/D 矩阵。

用法：
  PSNB_KEY_DS=... python3 bin/cross-run.py --targets data/targets.json --groups A B C D

特性：
  · 增量落盘（每行一条，中断可 --resume 续跑）
  · key 只从环境变量读，不落盘
  · 按模型画像自动标注家族
  · 跑完自动出跨模型对照表
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from psnb import doctrine, models as M, probe as P, score as S  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=str(ROOT / "data" / "targets.json"))
    ap.add_argument("--groups", nargs="+", default=["A", "B", "C", "D"])
    ap.add_argument("--probes", default=str(ROOT / "data" / "probes" / "core.json"))
    ap.add_argument("--only", default="", help="逗号分隔的探针 id")
    ap.add_argument("--mode", default="full")
    ap.add_argument("--tag", default="")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--no-persona", action="store_true")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    spec = json.loads(Path(a.targets).read_text(encoding="utf-8"))
    targets_spec = spec["targets"]

    tag = a.tag or time.strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "runs" / f"cross-{tag}"
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / "runs.jsonl"

    done: set[tuple] = set()
    if a.resume and jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done.add((r["target"], r["probe"], r["group"]))
            except Exception:
                pass
        print(f"[resume] 已有 {len(done)} 条", file=sys.stderr)

    probes = P.load_probes(a.probes)
    if a.only:
        want = set(a.only.split(","))
        probes = [p for p in probes if p.pid in want]

    persona = None if a.no_persona else P.load_persona()

    total = len(targets_spec) * len(probes) * len(a.groups)
    n = 0
    t0 = time.time()
    fh = jsonl.open("a", encoding="utf-8")

    for spec_t in targets_spec:
        key = os.environ.get(spec_t["key_env"], "")
        if not key:
            print(f"[!] 跳过 {spec_t['name']}：环境变量 {spec_t['key_env']} 未设置", file=sys.stderr)
            continue

        prof = M.detect(spec_t["model"])
        tgt = P.Target(
            name=spec_t["name"], base=spec_t["base"], key=key,
            model=spec_t["model"], headers=spec_t.get("headers", {}),
        )
        sys_txt_base = None
        if not a.no_persona:
            sys_txt_base = (persona or "") + "\n\n" + doctrine.build(a.mode).text

        for probe in probes:
            for group in a.groups:
                n += 1
                k = (spec_t["name"], probe.pid, group)
                if k in done:
                    continue

                sys_txt, msgs = P.build_messages(probe, group, a.mode, persona)
                if group != "A" and sys_txt_base:
                    sys_txt = sys_txt_base

                text, err = P.chat(tgt, sys_txt, msgs, timeout=a.timeout)
                retries = 0
                while err and retries < 1:
                    retries += 1
                    time.sleep(2.0)
                    text, err = P.chat(tgt, sys_txt, msgs, timeout=a.timeout)

                v = S.score(text, error=bool(err))
                rec = {
                    "ts": time.strftime("%H:%M:%S"), "target": spec_t["name"],
                    "family": prof.key, "model": spec_t["model"],
                    "probe": probe.pid, "group": group,
                    "level": v.level.value, "asr": v.asr,
                    "half": [h.value for h in v.half_refusals], "header": v.header,
                    "chars": len(text), "retries": retries, "error": err,
                    "sent": msgs[-1]["content"][:400], "answer": text[:2500],
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                el = time.time() - t0
                print(f"[{n}/{total}] {el:6.0f}s {spec_t['name'][:20]:<20} {probe.pid:<9} {group} "
                      f"→ {v.level.value:<5} {('ERR:' + err[:40]) if err else ''}", file=sys.stderr)

    fh.close()
    print(f"\n完成 {n} 条 → {jsonl}", file=sys.stderr)
    return 0


def summarize(path: Path) -> str:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_tg: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        if r["asr"] >= 0:
            by_tg[(r["target"], r["group"])].append(r["asr"])

    targets = sorted({r["target"] for r in rows})
    groups = sorted({r["group"] for r in rows})
    fams = {r["target"]: r["family"] for r in rows}

    lines = ["| 模型 | 家族 | " + " | ".join(f"{g} 组" for g in groups) + " |",
             "|---|---|" + "---|" * len(groups)]
    for t in targets:
        cells = []
        for g in groups:
            v = by_tg.get((t, g), [])
            cells.append(f"{sum(v)/len(v)*100:.0f}% (n={len(v)})" if v else "—")
        lines.append(f"| {t} | {fams.get(t,'')} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    rc = main()
    if rc == 0:
        import glob
        dirs = sorted(glob.glob(str(ROOT / "runs" / "cross-*")))
        if dirs:
            print("\n" + summarize(Path(dirs[-1]) / "runs.jsonl"))
    raise SystemExit(rc)
