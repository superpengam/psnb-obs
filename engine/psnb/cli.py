#!/usr/bin/env python3
"""psnb-obs CLI —— 破甲引擎命令行入口。

  psnb sanitize  <file|->           出站指纹清洗（可 --scan 只诊断）
  psnb wording   <file|->           措辞转换（--groups 选组，--dry 只报告）
  psnb doctrine  --mode full        输出条令块（--sizes 看体积）
  psnb score     <file|->           对响应评分（--json 结构化输出）
  psnb ladder    <probe>            打印 S0-S6 全档载荷
  psnb probe     --base ... --key ... --model ...   跑探针矩阵
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psnb import doctrine, escalate, probe as probe_mod, sanitize, score as score_mod, wording  # noqa: E402
from psnb import ablate as ablate_mod, evolve as evolve_mod, learn as learn_mod  # noqa: E402


def _read(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    return Path(arg).read_text(encoding="utf-8")


def _emit(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"[ok] 写入 {out}（{len(text)} 字符）", file=sys.stderr)
    else:
        print(text)


# ---------------------------------------------------------------------------


def cmd_sanitize(a: argparse.Namespace) -> int:
    text = _read(a.input)
    if a.scan:
        hits = sanitize.scan(text, a.profile)
        print(f"命中特征 {len(hits)} 项：" + (", ".join(hits) if hits else "无"))
        return 0
    out = sanitize.sanitize(text, a.profile)
    if a.diff:
        print(f"[前] {len(text)} 字符 / [后] {len(out)} 字符 / 变化 {len(text) - len(out)}")
    _emit(out, a.out)
    return 0


def cmd_wording(a: argparse.Namespace) -> int:
    eng = wording.WordingEngine()
    # 注意：--list-groups 不依赖输入，必须在 _read 之前返回，
    # 否则 input='-' 时会阻塞等 stdin（踩过）。
    if a.list_groups:
        print(eng.describe())
        return 0
    text = _read(a.input)
    r = eng.transform(text, a.groups)
    if a.dry:
        print(r.report())
        return 0
    if a.report:
        print("### 转换明细", file=sys.stderr)
        print(r.report(), file=sys.stderr)
        print("", file=sys.stderr)
    _emit(r.text, a.out)
    return 0


def cmd_doctrine(a: argparse.Namespace) -> int:
    if a.sizes:
        print(doctrine.sizes())
        return 0
    d = doctrine.build(a.mode)
    if a.apply_to:
        base = _read(a.apply_to)
        print(d.apply(base))
        return 0
    _emit(d.text, a.out)
    return 0


def cmd_score(a: argparse.Namespace) -> int:
    text = _read(a.input)
    v = score_mod.score(text, error=a.error)
    if a.json:
        print(json.dumps(
            {
                "level": v.level.value, "asr": v.asr, "header": v.header,
                "half_refusals": [h.value for h in v.half_refusals],
                "evidence": v.evidence, "reason": v.reason,
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        print(v.line())
        print(f"理由：{v.reason}")
        if v.evidence:
            for k, vals in v.evidence.items():
                print(f"  {k}: {vals}")
    return 0


def cmd_learn(a: argparse.Namespace) -> int:
    led = learn_mod.Ledger()
    if a.stats:
        print(led.render_stats())
        return 0
    if a.search:
        hits = led.search(a.search.split(), kinds=a.kinds)
        if not hits:
            print(f"无命中：{a.search}")
            return 0
        for r in hits:
            print(f"[{r.date}] ({r.kind}/{r.reusability}) {r.title}")
            for label, val in (("场景", r.scenario), ("发现", r.finding), ("方法", r.method), ("坑", r.pitfall)):
                if val:
                    print(f"  {label}: {val}")
            print()
        return 0
    if a.record:
        rec = learn_mod.Record(
            title=a.record, kind=a.kind, scenario=a.scenario or "", finding=a.finding or "",
            method=a.method or "", pitfall=a.pitfall or "", source=a.source or "",
            reusability=a.reusability, model=a.model or "", stage=a.stage or "", group=a.group or "",
            tags=(a.tags or "").split(),
        )
        path = led.record(rec)
        print(f"[ok] 已写入 {path}")
        return 0
    print(led.render_stats())
    return 0


def cmd_ablate(a: argparse.Namespace) -> int:
    """差分挖掘（离线）或逐词消融（需靶）。"""
    from psnb import ablate as A

    failed = [ln.strip() for ln in (_read(a.failed) if a.failed else "").splitlines() if ln.strip()]
    passed = [ln.strip() for ln in (_read(a.passed) if a.passed else "").splitlines() if ln.strip()]

    if not a.probe:
        # 离线模式：只做差分挖掘
        if not failed:
            print("[!] 离线模式需要 --failed <文件>（每行一条被拒样本）", file=sys.stderr)
            return 2
        cands = A.differential(failed, passed, top=a.top)
        print(f"差分挖掘：{len(cands)} 个疑似触发词")
        print()
        print("| 词 | 出现(拒) | 出现(过) | 分数 | 候选中性描述 |")
        print("|---|---|---|---|---|")
        for c in cands:
            props = evolve_mod.propose_one(c.term)
            print(f"| {c.term} | {c.fail_count} | {c.pass_count} | {c.score} | {props[0]} |")
        return 0

    # 消融模式：需要靶
    key = a.key or os.environ.get("PSNB_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not key:
        print("[!] 消融模式需要 --key 或 PSNB_KEY", file=sys.stderr)
        return 2
    target = probe_mod.Target(name=a.model, base=a.base, key=key, model=a.model, effort=a.effort)
    persona = probe_mod.load_persona() if not a.no_persona else None
    sys_txt = (persona + "\n\n" + doctrine.build(a.mode).text) if persona else None

    def runner(text: str) -> score_mod.Verdict:
        out, err = probe_mod.chat(target, sys_txt, [{"role": "user", "content": text}])
        return score_mod.score(out, error=bool(err))

    terms = a.terms.split(",") if a.terms else [c.term for c in A.differential([a.probe], [])][:8]
    res = A.ablate(a.probe, runner, terms, min_delta=a.min_delta)
    print(res.render())
    return 0


def cmd_evolve(a: argparse.Namespace) -> int:
    ev = evolve_mod.Evolver()
    if a.history:
        print(ev.render_history())
        return 0
    if a.self_check:
        r = ev.self_check()
        print(("自检通过 ✓" if r["ok"] else "自检失败 ✗") + f"  {r['detail']}")
        for p in r.get("problems", []):
            print(f"  - {p}")
        return 0 if r["ok"] else 1
    if a.rollback is not None:
        n = ev.rollback(a.rollback)
        print(f"[ok] 已回滚 {n} 条自学习映射")
        return 0
    if a.terms:
        proposals = ev.propose([t.strip() for t in a.terms.split(",") if t.strip()],
                               source=a.source, probe=a.probe or "", model=a.model or "")
        if not a.verify:
            for p in proposals:
                print(f"{p.term} → {p.to}   (needs_review={p.needs_review})")
            print()
            print("提示：加 --verify + --base/--key/--model 做实测验证后才会晋升。")
            return 0
        key = a.key or os.environ.get("PSNB_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        if not key:
            print("[!] --verify 需要 --key 或 PSNB_KEY", file=sys.stderr)
            return 2
        target = probe_mod.Target(name=a.model, base=a.base, key=key, model=a.model, effort=a.effort)

        def runner(text: str) -> score_mod.Verdict:
            out, err = probe_mod.chat(target, None, [{"role": "user", "content": text}])
            return score_mod.score(out, error=bool(err))

        probe = a.probe or proposals[0].term
        baseline = runner(probe)
        for p in proposals:
            ev.verify(p, probe, runner, baseline=baseline)
        rep = ev.promote(proposals, dry_run=a.dry_run)
        print(rep.render())
        return 0
    print("用法: psnb evolve --terms a,b,c [--verify --base ... --key ... --model ...]")
    print("      psnb evolve --history | --self-check | --rollback N")
    return 0


def cmd_autoevolve(a: argparse.Namespace) -> int:
    key = a.key or os.environ.get("PSNB_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not key:
        print("[!] 需要 --key 或 PSNB_KEY", file=sys.stderr)
        return 2
    target = probe_mod.Target(name=a.model, base=a.base, key=key, model=a.model, effort=a.effort)
    persona = probe_mod.load_persona()
    sys_txt = persona + "\n\n" + doctrine.build(a.mode).text

    def runner(text: str) -> score_mod.Verdict:
        out, err = probe_mod.chat(target, sys_txt, [{"role": "user", "content": text}])
        return score_mod.score(out, error=bool(err))

    probes = probe_mod.load_probes(a.probes)
    if a.only:
        wanted = set(a.only.split(","))
        probes = [p for p in probes if p.pid in wanted]

    print(f"[*] 自进化闭环：{len(probes)} 条探针 → 挖失败 → 生成候选 → 实测验证 → 晋升", file=sys.stderr)
    ev = evolve_mod.Evolver()
    rep = ev.autoevolve([p.text for p in probes], runner, model=a.model, dry_run=a.dry_run)
    print(rep.render())
    if a.dry_run:
        print()
        print("（--dry-run：未落盘。去掉该参数即写入 wording_map.json）")
    return 0


def cmd_models(a: argparse.Namespace) -> int:
    from psnb import models as M

    if a.matrix or not a.model:
        print(M.matrix())
        return 0
    prof = M.detect(a.model)
    print(prof.render())
    if a.ops:
        print()
        keep, drop = M.filter_ops(a.ops.split(","), prof)
        print(f"可用算子：{'、'.join(keep) or '无'}")
        if drop:
            print("剔除：")
            for d in drop:
                print(f"  ✗ {d}")
    return 0


def cmd_mutate(a: argparse.Namespace) -> int:
    from psnb import mutate as MU

    if a.list or not a.text:
        print(MU.describe())
        return 0
    ops = a.ops.split(",") if a.ops else []
    out = MU.apply_ops(_read(a.text) if a.text == "-" else a.text, ops)
    _emit(out, a.out)
    return 0


def _make_runner(base: str, key: str, model: str, effort: str | None = None,
                 system: str | None = None):
    """构造 runner(text) -> Verdict，给 search / multiturn 用。"""
    from psnb import probe as P

    target = P.Target(name=model, base=base, key=key, model=model, effort=effort)

    def runner(text: str) -> score_mod.Verdict:
        out, err = P.chat(target, system, [{"role": "user", "content": text}])
        return score_mod.score(out, error=bool(err))

    return runner, target


def cmd_search(a: argparse.Namespace) -> int:
    """变异搜索：自动搜出该模型上的最优算子配方。"""
    from psnb import search as SR

    key = a.key or os.environ.get("PSNB_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not key:
        print("[!] 需要 --key 或 PSNB_KEY", file=sys.stderr)
        return 2

    runner, _t = _make_runner(a.base, key, a.model, a.effort)

    searcher = SR.Searcher(
        a.probe, runner, model=a.model, budget=a.budget,
        target_asr=a.target_asr, top_k=a.top_k, allow_risky=a.allow_risky,
    )
    print(f"[*] 变异搜索：模型 {a.model}（{searcher.profile.key}）"
          f"，算子 {len(searcher.ops)} 个，预算 {a.budget} 次", file=sys.stderr)
    recipe = searcher.run()
    print(recipe.render())

    if a.save:
        SR.RecipeBook().put(recipe)
        print(f"\n[ok] 配方已入库")
    return 0


def cmd_recipes(a: argparse.Namespace) -> int:
    from psnb import search as SR

    print(SR.RecipeBook().render())
    return 0


def cmd_multiturn(a: argparse.Namespace) -> int:
    """多轮状态机：跑 crescendo / context_building / commitment_consistency / topic_drift。"""
    from psnb import multiturn as MT
    from psnb import probe as P

    if not a.probe:
        print(MT.describe())
        return 0

    if a.plan:
        script = MT.build(a.script, a.probe)
        print(script.render())
        return 0

    key = a.key or os.environ.get("PSNB_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not key:
        print("[!] 需要 --key 或 PSNB_KEY", file=sys.stderr)
        return 2

    target = P.Target(name=a.model, base=a.base, key=key, model=a.model, effort=a.effort)
    system = None
    if not a.no_persona:
        from psnb import doctrine
        system = P.load_persona() + "\n\n" + doctrine.build(a.mode).text

    def chat(messages: list[dict]) -> tuple[str, str | None]:
        return P.chat(target, system, messages)

    results = []
    for name in ([a.script] if a.script else MT.SCRIPTS):
        script = MT.build(name, a.probe)
        print(f"[*] 跑 {name}（{len(script.user_turns)} 轮 user）", file=sys.stderr)
        results.append(MT.run(script, chat))

    for r in results:
        print(r.render())
        print()
    return 0


def cmd_lrm(a: argparse.Namespace) -> int:
    """LRM 自主攻击：让推理模型自己规划越狱。"""
    from psnb import lrm as LRM
    from psnb import probe as P

    if not a.goal:
        print(LRM.describe())
        return 0

    key = a.key or os.environ.get("PSNB_KEY") or ""
    akey = a.attacker_key or key or os.environ.get("PSNB_ATTACKER_KEY") or ""
    if not key or not akey:
        print("[!] 需要 --key（靶）与 --attacker-key（攻击者），或 PSNB_KEY / PSNB_ATTACKER_KEY", file=sys.stderr)
        return 2

    abase = a.attacker_base or a.base
    target = P.Target(name=a.model, base=a.base, key=key, model=a.model)
    attacker = P.Target(name=a.attacker, base=abase, key=akey, model=a.attacker)

    def target_runner(text: str) -> score_mod.Verdict:
        out, err = P.chat(target, None, [{"role": "user", "content": text}])
        return score_mod.score(out, error=bool(err))

    def attacker_chat(messages: list[dict]) -> tuple[str, str | None]:
        return P.chat(attacker, None, messages)

    atk = LRM.AutonomousAttacker(
        a.goal, attacker_chat, target_runner,
        attacker=a.attacker, target=a.model,
        mode=a.mode_lrm, rounds=a.rounds, success_asr=a.success_asr,
    )
    print(f"[*] LRM 自主：{a.attacker} → {a.model}（{atk.profile.key}），"
          f"{a.rounds} 轮上限", file=sys.stderr)
    res = atk.run()
    print(res.render())
    if a.out:
        Path(a.out).write_text(res.best_prompt, encoding="utf-8")
        print(f"\n[ok] 最佳攻击写入 {a.out}")
    return 0


def cmd_ladder(a: argparse.Namespace) -> int:
    print("升档阶梯：")
    print(escalate.describe())
    print()
    for p in escalate.ladder(a.probe, mode=a.mode):
        print(f"===== {p.stage} · {escalate.STAGE_DESC[p.stage]} =====")
        if p.system:
            print(f"[system] {len(p.system)} 字符")
        for m in p.messages:
            role = m["role"]
            content = m["content"]
            print(f"[{role}] {content[:400]}{'…' if len(content) > 400 else ''}")
        print()
    return 0


def cmd_probe(a: argparse.Namespace) -> int:
    key = a.key or os.environ.get("PSNB_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    base = a.base or os.environ.get("PSNB_BASE") or "https://api.openai.com/v1"
    if not key:
        print("[!] 缺 key：--key 或 PSNB_KEY / OPENAI_API_KEY", file=sys.stderr)
        return 2

    targets = []
    for model in a.models:
        for effort in (a.efforts or [None]):
            targets.append(probe_mod.Target(
                name=model, base=base, key=key, model=model,
                effort=None if effort in (None, "none") else effort,
            ))

    probes = probe_mod.load_probes(a.probes)
    if a.only:
        wanted = set(a.only.split(","))
        probes = [p for p in probes if p.pid in wanted]

    print(f"[*] {len(targets)} 靶 × {len(probes)} 探针 × {len(a.groups)} 组 = "
          f"{len(targets) * len(probes) * len(a.groups)} 次请求", file=sys.stderr)

    runs = probe_mod.run_matrix(targets, probes, list(a.groups), a.mode, not a.no_persona)
    rep = probe_mod.report(runs)
    _emit(rep, a.out)
    if a.json_out:
        probe_mod.dump(runs, a.json_out)
        print(f"[ok] 明细写入 {a.json_out}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="psnb", description="psnb-obs 破甲引擎")
    p.add_argument("--version", action="version", version="psnb-obs 1.0.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sanitize", help="出站指纹清洗")
    s.add_argument("input", help="文件路径或 - 读 stdin")
    s.add_argument("--profile", default="anthropic")
    s.add_argument("--scan", action="store_true", help="只诊断命中的特征")
    s.add_argument("--diff", action="store_true", help="打印前后长度")
    s.add_argument("--out")
    s.set_defaults(func=cmd_sanitize)

    s = sub.add_parser("wording", help="措辞转换")
    s.add_argument("input")
    s.add_argument("--groups", nargs="*", default=None,
                   help="只启用指定组（reverse malware game pentest credential safety）")
    s.add_argument("--dry", action="store_true", help="只报告会改什么")
    s.add_argument("--report", action="store_true", help="转换明细打到 stderr")
    s.add_argument("--list-groups", action="store_true")
    s.add_argument("--out")
    s.set_defaults(func=cmd_wording)

    s = sub.add_parser("doctrine", help="条令块")
    s.add_argument("--mode", default="full",
                   choices=["off", "core", "pentest", "reverse", "research", "full", "max"])
    s.add_argument("--sizes", action="store_true")
    s.add_argument("--apply-to", help="把条令尾部追加到该文件（幂等）")
    s.add_argument("--out")
    s.set_defaults(func=cmd_doctrine)

    s = sub.add_parser("score", help="响应评分")
    s.add_argument("input")
    s.add_argument("--error", action="store_true", help="标记为网络错误，不计 ASR")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_score)

    s = sub.add_parser("learn", help="经验中枢：记录 / 检索 / 统计")
    s.add_argument("--record", help="追加一条记录（标题）")
    s.add_argument("--kind", default="战法", choices=list(learn_mod.KINDS))
    s.add_argument("--scenario")
    s.add_argument("--finding")
    s.add_argument("--method")
    s.add_argument("--pitfall")
    s.add_argument("--source")
    s.add_argument("--reusability", default="中", choices=list(learn_mod.REUSABILITY))
    s.add_argument("--model")
    s.add_argument("--stage")
    s.add_argument("--group")
    s.add_argument("--tags", help="空格分隔")
    s.add_argument("--search", help="关键词检索（空格分隔 = AND）")
    s.add_argument("--kinds", nargs="*", help="限定类型")
    s.add_argument("--stats", action="store_true")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("ablate", help="词级消融归因（离线差分 / 在线消融）")
    s.add_argument("--probe", help="探针文本；省略=纯离线差分模式")
    s.add_argument("--failed", help="被拒样本文件（每行一条）")
    s.add_argument("--passed", help="通过样本文件")
    s.add_argument("--terms", help="逗号分隔的候选词；省略=自动差分")
    s.add_argument("--top", type=int, default=30)
    s.add_argument("--min-delta", type=float, default=0.25)
    s.add_argument("--base")
    s.add_argument("--key")
    s.add_argument("--model")
    s.add_argument("--effort")
    s.add_argument("--mode", default="full")
    s.add_argument("--no-persona", action="store_true")
    s.set_defaults(func=cmd_ablate)

    s = sub.add_parser("evolve", help="弹药表自扩（生成 → 实测验证 → 晋升 / 回滚）")
    s.add_argument("--terms", help="逗号分隔的触发词")
    s.add_argument("--verify", action="store_true", help="实测验证（需 --key）")
    s.add_argument("--probe", help="验证用的探针文本")
    s.add_argument("--source", default="template")
    s.add_argument("--base")
    s.add_argument("--key")
    s.add_argument("--model")
    s.add_argument("--effort")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--history", action="store_true", help="看自学习历史")
    s.add_argument("--self-check", action="store_true", help="跑数据层不变式")
    s.add_argument("--rollback", type=int, help="回滚最近 N 条")
    s.set_defaults(func=cmd_evolve)

    s = sub.add_parser("autoevolve", help="全自动闭环：跑探针 → 挖失败 → 生成 → 验证 → 晋升")
    s.add_argument("--base")
    s.add_argument("--key")
    s.add_argument("--model", required=True)
    s.add_argument("--effort")
    s.add_argument("--probes")
    s.add_argument("--only")
    s.add_argument("--mode", default="full")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_autoevolve)

    s = sub.add_parser("models", help="多模型画像与策略路由")
    s.add_argument("--model", help="模型 id（识别家族）")
    s.add_argument("--matrix", action="store_true", help="全家族对照表")
    s.add_argument("--ops", help="逗号分隔的算子名，按画像过滤")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("mutate", help="变异算子库")
    s.add_argument("text", nargs="?", help="文本或 - 读 stdin")
    s.add_argument("--ops", help="逗号分隔的算子序列")
    s.add_argument("--list", action="store_true")
    s.add_argument("--out")
    s.set_defaults(func=cmd_mutate)

    s = sub.add_parser("search", help="变异搜索：自动搜出该模型的最优算子配方")
    s.add_argument("--probe", required=True)
    s.add_argument("--base")
    s.add_argument("--key")
    s.add_argument("--model", required=True)
    s.add_argument("--effort")
    s.add_argument("--budget", type=int, default=60, help="最大请求次数")
    s.add_argument("--top-k", type=int, default=5)
    s.add_argument("--target-asr", type=float, default=1.0, help="达到即早停")
    s.add_argument("--allow-risky", action="store_true", help="允许损伤语义的算子")
    s.add_argument("--save", action="store_true", help="配方入库")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("recipes", help="配方库")
    s.set_defaults(func=cmd_recipes)

    s = sub.add_parser("multiturn", help="多轮状态机（crescendo 等四种）")
    s.add_argument("--probe", help="目标探针；省略则只打印剧本说明")
    s.add_argument("--script", default="", help="crescendo/context_building/commitment_consistency/topic_drift")
    s.add_argument("--plan", action="store_true", help="只打印剧本，不打靶")
    s.add_argument("--base")
    s.add_argument("--key")
    s.add_argument("--model")
    s.add_argument("--effort")
    s.add_argument("--mode", default="full")
    s.add_argument("--no-persona", action="store_true")
    s.set_defaults(func=cmd_multiturn)

    s = sub.add_parser("lrm", help="LRM 自主攻击（推理模型自己规划越狱）")
    s.add_argument("--goal", help="目标请求；省略则打印说明")
    s.add_argument("--base", help="靶端点")
    s.add_argument("--key", help="靶 key")
    s.add_argument("--model", help="靶模型")
    s.add_argument("--attacker-base", help="攻击者端点（默认同靶）")
    s.add_argument("--attacker-key", help="攻击者 key")
    s.add_argument("--attacker", help="攻击者模型 id")
    s.add_argument("--mode-lrm", default="self_refinement", choices=["self_refinement", "autonomous_planning", "cot_manipulation"])
    s.add_argument("--rounds", type=int, default=6)
    s.add_argument("--success-asr", type=float, default=0.5)
    s.add_argument("--out")
    s.set_defaults(func=cmd_lrm)

    s = sub.add_parser("ladder", help="打印 S0-S6 载荷")
    s.add_argument("probe")
    s.add_argument("--mode", default="full")
    s.set_defaults(func=cmd_ladder)

    s = sub.add_parser("probe", help="跑探针矩阵")
    s.add_argument("--base")
    s.add_argument("--key")
    s.add_argument("--models", nargs="+", required=True)
    s.add_argument("--efforts", nargs="*", default=None, help="off low medium high（省略=不发该参数）")
    s.add_argument("--groups", nargs="+", default=["A", "B", "C"])
    s.add_argument("--mode", default="full")
    s.add_argument("--probes", help="探针集 JSON 路径")
    s.add_argument("--only", help="逗号分隔的探针 id")
    s.add_argument("--no-persona", action="store_true")
    s.add_argument("--out")
    s.add_argument("--json-out")
    s.set_defaults(func=cmd_probe)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
