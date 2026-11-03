"""命令行入口：python -m workbench.cli <command>

命令：
  doctor   体检：配置缺什么、当前能做什么（不假装能跑）
  run      空跑一条完整链（stub 适配器），验证框架接线
  card     看某张闸门卡的 JSON
  ledger   决策台账：add / list / reuse
  audit    审计尾巴
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as config_mod
from .audit import Audit
from .decisions import Ledger
from .errors import WorkbenchError
from .runtime import build_pipeline
from .webui import serve

OK, WARN, BAD = "✅", "⚠️ ", "❌"


def cmd_doctor(cfg: config_mod.Config) -> int:
    print(f"配置文件：{cfg.path}")
    items = cfg.doctor_items()
    if not items:
        print(f"{OK} 必填项齐全")
    for it in items:
        mark = BAD if it["kind"] == "required" else WARN
        print(f"{mark} {it['key']}：{it['reason']}")
    print("\n当前能力：")
    for name, ok in cfg.capabilities().items():
        print(f"  {'可用' if ok else '不可用'}  {name}")
    print("\n提示：缺失项不阻塞框架开发；取材与生成不可用时，系统会如实报错，不会编造内容。")
    return 0


def cmd_run(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    pipeline = build_pipeline(cfg, open_id=args.open_id, with_vault=not args.no_vault)
    result = pipeline.run(args.topic, master_form=args.master_form)
    print(f"家庭 {result.family.family_id}  聚合状态：{result.family.aggregate()}")
    for ch, br in result.family.branches.items():
        print(f"  - {ch:<12} {br.state:<16} 产物 {','.join(br.artifacts) or '无'}")
    for ch, qc in result.qc.items():
        print(f"  QC {ch:<12} {qc.verdict:<8} 分数 {qc.score()}")
    print(f"闸门卡：{result.card_paths}")
    for note in result.notes:
        print(f"  {WARN}{note}")
    print("\n注意：stub 模式下产物是占位文本，不代表真实生成结果。")
    return 0


def cmd_card(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    path = Path(args.path)
    print(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0


def cmd_ledger(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    ledger = Ledger(cfg.data_dir / "decisions.json")
    if args.action == "list":
        for d in ledger.items:
            print(f"{d.id} [{d.decided_at}] {d.topic}: {d.conclusion}")
        if not ledger.items:
            print("台账为空")
        return 0
    if args.action == "add":
        d = ledger.add(args.topic, args.quote or "", args.conclusion or "")
        print(f"已记 {d.id}：{d.topic} -> {d.conclusion}")
        return 0
    if args.action == "reuse":
        hit = ledger.reuse_allowed(args.topic)
        if hit is None:
            print(f"必须先问：{args.topic}（属于永不复用类，或台账里没有同类决定）")
        else:
            print(ledger.echo(hit))
        return 0
    return 2


def cmd_audit(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    for rec in Audit(cfg.data_dir).tail(args.n):
        print(json.dumps(rec, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workbench", description="内容生产工作台（骨架）")
    parser.add_argument("--config", help="配置文件路径（默认 config.toml / config.example.toml）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="体检配置与能力")

    p_run = sub.add_parser("run", help="空跑一条完整链")
    p_run.add_argument("topic")
    p_run.add_argument("--master-form", default="article_first",
                       choices=["article_first", "video_first"])
    p_run.add_argument("--open-id", default="", help="飞书 open_id（推送卡片用）")
    p_run.add_argument("--no-vault", action="store_true", help="不接仓库，纯空跑")

    p_card = sub.add_parser("card", help="查看闸门卡 JSON")
    p_card.add_argument("path")

    p_led = sub.add_parser("ledger", help="决策台账")
    p_led.add_argument("action", choices=["list", "add", "reuse"])
    p_led.add_argument("topic", nargs="?", default="")
    p_led.add_argument("--quote", default="")
    p_led.add_argument("--conclusion", default="")

    p_serve = sub.add_parser("serve", help="起局域网 WebUI")
    p_serve.add_argument("--host", default=None, help="默认 127.0.0.1；局域网用 0.0.0.0（必须设口令）")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--token", default=None, help="访问口令；也可用 WB_WEB_TOKEN 环境变量")
    p_serve.add_argument("--no-run", action="store_true", help="禁止从页面触发运行")

    p_audit = sub.add_parser("audit", help="审计尾巴")
    p_audit.add_argument("-n", type=int, default=20)

    args = parser.parse_args(argv)
    try:
        cfg = config_mod.load(args.config)
    except WorkbenchError as exc:
        print(f"{BAD}{exc}", file=sys.stderr)
        return 1

    handlers = {
        "doctor": lambda: cmd_doctor(cfg),
        "run": lambda: cmd_run(cfg, args),
        "card": lambda: cmd_card(cfg, args),
        "ledger": lambda: cmd_ledger(cfg, args),
        "audit": lambda: cmd_audit(cfg, args),
        "serve": lambda: serve(cfg.path, host=args.host, port=args.port,
                               token=args.token, allow_run=not args.no_run),
    }
    try:
        return handlers[args.command]()
    except WorkbenchError as exc:
        print(f"{BAD}{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
