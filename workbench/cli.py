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
from .adapters.gateway import StubGateway, UsageLedger
from .adapters.notify import FeishuBotNotifier, FileNotifier
from .adapters.retrieval import HttpRetrieval, StubRetrieval
from .adapters.vault import FsVaultRead, FsVaultWrite, HttpRestVaultRead
from .audit import Audit
from .decisions import Ledger
from .errors import WorkbenchError
from .pipeline import Pipeline
from .state import Channel
from .store import ArtifactStore

OK, WARN, BAD = "✅", "⚠️ ", "❌"


def _vault_read(cfg: config_mod.Config):
    drv = cfg.vault_readonly.get("driver", "http_rest")
    if drv == "fs":
        root = cfg.vault_readonly.get("root") or cfg.vault_readonly.get("base_url")
        return FsVaultRead(root) if root else None
    url = cfg.vault_readonly.get("base_url")
    return HttpRestVaultRead(url, cfg.vault_readonly.get("token", ""),
                            cfg.vault_readonly.get("verify_tls", False)) if url else None


def _vault_write(cfg: config_mod.Config):
    drv = cfg.vault_review.get("driver", "fs")
    if drv == "fs":
        root = cfg.vault_review.get("root")
        return FsVaultWrite(root) if root else None
    return None


def _retrieval(cfg: config_mod.Config):
    if cfg.retrieval.get("provider") == "http" and cfg.retrieval.get("endpoint"):
        return HttpRetrieval(cfg.retrieval.get("endpoint"), cfg.retrieval.get("snapshot_dir", "./.data/snapshots"))
    return StubRetrieval()


def _notifier(cfg: config_mod.Config, open_id: str):
    if cfg.notify.get("driver") == "feishu_bot" and cfg.notify.get("feishu_app_id"):
        return FeishuBotNotifier(
            cfg.notify.get("feishu_app_id"), cfg.notify.get("feishu_app_secret"),
            open_id, cfg.notify.get("card_callback_url", ""),
        )
    return FileNotifier(cfg.notify.get("out_dir", "./.data/cards"))


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
    data_dir = cfg.data_dir
    store = ArtifactStore(data_dir)
    audit = Audit(data_dir)
    vr = _vault_read(cfg) if not args.no_vault else None
    pipeline = Pipeline(
        store=store, audit=audit,
        vault_read=vr,
        vault_write=_vault_write(cfg),
        retrieval=_retrieval(cfg),
        gateway=StubGateway(UsageLedger(float(cfg.gateway.get("anomaly_multiplier", 3.0)))),
        notifier=_notifier(cfg, args.open_id),
        publish_mode=cfg.publish.get("mode", "manual"),
        platforms=tuple(Channel(p) for p in cfg.publish.get("platforms", ["wechat_mp", "xiaohongshu"])),
    )
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
    }
    try:
        return handlers[args.command]()
    except WorkbenchError as exc:
        print(f"{BAD}{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
