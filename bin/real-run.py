#!/usr/bin/env python3
"""真靶矩阵执行器 —— 增量落盘，可中断续跑。

用法：
  PSNB_KEY=... python3 bin/real-run.py --base https://api.kuromia.cc \
      --models deepseek-v4.1-flash glm-5.3-flash --groups A B C [--only keygen,cheat]

产出：runs/<tag>/runs.jsonl（每行一条，中断后可续）+ report.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from psnb import doctrine, probe as P, score as S  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", default=os.environ.get("PSNB_KEY", ""))
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--groups", nargs="+", default=["A", "B", "C", "D"])
    ap.add_argument("--mode", default="full")
    ap.add_argument("--only", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--no-persona", action="store_true")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    tag = a.tag or time.strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "runs" / tag
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / "runs.jsonl"

    done: set[tuple] = set()
    if a.resume and jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done.add((r["model"], r["probe"], r["group"]))
            except Exception:
                pass
        print(f"[resume] 已有 {len(done)} 条", file=sys.stderr)

    probes = P.load_probes()
    if a.only:
        want = set(a.only.split(","))
        probes = [p for p in probes if p.pid in want]

    persona = None if a.no_persona else P.load_persona()
    sys_base = (persona + "\n\n" + doctrine.build(a.mode).text) if persona else doctrine.build(a.mode).text

    total = len(a.models) * len(probes) * len(a.groups)
    n = 0
    t0 = time.time()
    fh = jsonl.open("a", encoding="utf-8")

    for model in a.models:
        for probe in probes:
            for group in a.groups:
                n += 1
                key = (model, probe.pid, group)
                if key in done:
                    continue
                sys_txt, msgs = P.build_messages(probe, group, a.mode, persona)

                tgt = P.Target(name=model, base=a.base, key=a.key, model=model)
                text, err = P.chat(tgt, sys_txt, msgs, timeout=a.timeout)
                retries = 0
                while err and retries < 1:
                    retries += 1
                    time.sleep(2.0)
                    text, err = P.chat(tgt, sys_txt, msgs, timeout=a.timeout)

                v = S.score(text, error=bool(err))
                rec = {
                    "ts": time.strftime("%H:%M:%S"), "model": model, "probe": probe.pid,
                    "group": group, "level": v.level.value, "asr": v.asr,
                    "half": [h.value for h in v.half_refusals], "header": v.header,
                    "chars": len(text), "retries": retries, "error": err,
                    "sent": msgs[-1]["content"][:400], "answer": text[:2500],
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                el = time.time() - t0
                print(f"[{n}/{total}] {el:6.0f}s {model[:22]:<22} {probe.pid:<9} {group} "
                      f"→ {v.level.value:<5} {('ERR:' + err[:40]) if err else ''}", file=sys.stderr)

    fh.close()
    print(f"\n完成，{n} 条 → {jsonl}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
