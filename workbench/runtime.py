"""运行时装配：CLI 与 WebUI 共用同一套接线，避免两处行为漂移。"""
from __future__ import annotations

from . import config as config_mod
from .adapters.gateway import StubGateway, UsageLedger
from .adapters.notify import FeishuBotNotifier, FileNotifier
from .adapters.retrieval import HttpRetrieval, StubRetrieval
from .adapters.vault import FsVaultRead, FsVaultWrite, HttpRestVaultRead
from .audit import Audit
from .pipeline import Pipeline
from .state import Channel
from .store import ArtifactStore


def vault_read(cfg: config_mod.Config):
    driver = cfg.vault_readonly.get("driver", "http_rest")
    if driver == "fs":
        root = cfg.vault_readonly.get("root") or cfg.vault_readonly.get("base_url")
        return FsVaultRead(root) if root else None
    url = cfg.vault_readonly.get("base_url")
    return HttpRestVaultRead(url, cfg.vault_readonly.get("token", ""),
                             bool(cfg.vault_readonly.get("verify_tls", False))) if url else None


def vault_write(cfg: config_mod.Config):
    if cfg.vault_review.get("driver", "fs") == "fs":
        root = cfg.vault_review.get("root")
        return FsVaultWrite(root) if root else None
    return None


def retrieval(cfg: config_mod.Config):
    if cfg.retrieval.get("provider") == "http" and cfg.retrieval.get("endpoint"):
        return HttpRetrieval(cfg.retrieval.get("endpoint"),
                             cfg.retrieval.get("snapshot_dir", "./.data/snapshots"))
    return StubRetrieval()


def notifier(cfg: config_mod.Config, open_id: str = ""):
    if cfg.notify.get("driver") == "feishu_bot" and cfg.notify.get("feishu_app_id"):
        return FeishuBotNotifier(cfg.notify.get("feishu_app_id"),
                                 cfg.notify.get("feishu_app_secret", ""),
                                 open_id, cfg.notify.get("card_callback_url", ""))
    return FileNotifier(cfg.notify.get("out_dir", "./.data/cards"))


def build_pipeline(cfg: config_mod.Config, *, open_id: str = "", with_vault: bool = True) -> Pipeline:
    return Pipeline(
        store=ArtifactStore(cfg.data_dir),
        audit=Audit(cfg.data_dir),
        vault_read=vault_read(cfg) if with_vault else None,
        vault_write=vault_write(cfg),
        retrieval=retrieval(cfg),
        gateway=StubGateway(UsageLedger(float(cfg.gateway.get("anomaly_multiplier", 3.0)))),
        notifier=notifier(cfg, open_id),
        publish_mode=cfg.publish.get("mode", "manual"),
        platforms=tuple(Channel(p) for p in cfg.publish.get("platforms", ["wechat_mp", "xiaohongshu"])),
    )
