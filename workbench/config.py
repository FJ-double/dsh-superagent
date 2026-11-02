"""配置：全量走配置项（D45）。缺失不抛，由 doctor 汇总。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ENV = "WORKBENCH_CONFIG"
DEFAULT_PATHS = ("config.toml", "config.example.toml")


@dataclass
class Section:
    name: str
    values: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()

    def get(self, key: str, default: Any = None) -> Any:
        env_key = f"WB_{self.name.upper()}_{key.upper()}"
        if env_key in os.environ:
            return os.environ[env_key]
        value = self.values.get(key, default)
        return default if value == "" else value

    def missing(self) -> list[str]:
        """返回本段必填但为空的键，形如 'vault.readonly.base_url'。"""
        out = []
        for key in self.required:
            if not self.get(key):
                out.append(f"{self.name}.{key}")
        return out


@dataclass
class Config:
    path: Path
    app: Section
    vault_readonly: Section
    vault_review: Section
    retrieval: Section
    gateway: Section
    notify: Section
    publish: Section

    @property
    def data_dir(self) -> Path:
        return Path(self.app.get("data_dir", "./.data"))

    def missing_required(self) -> list[str]:
        return [m for m in self.doctor_items() if m["kind"] == "required"]

    def doctor_items(self) -> list[dict]:
        """体检清单。kind=required 阻塞对应能力；kind=optional 只提示。"""
        items: list[dict] = []

        def add(sec: Section, key: str, reason: str, kind: str) -> None:
            items.append(
                {
                    "kind": kind,
                    "key": f"{sec.name}.{key}",
                    "reason": reason,
                }
            )

        # 素材库：缺地址/token 则取材不可用（N2 阻塞），但不阻塞开发
        if not self.vault_readonly.get("base_url"):
            add(self.vault_readonly, "base_url", "素材库地址未填，取材不可用", "required")
        if self.vault_readonly.get("driver") == "http_rest" and not self.vault_readonly.get("token"):
            add(self.vault_readonly, "token", "素材库只读 token 未填", "required")

        # 复盘库：只影响写回
        if not (self.vault_review.get("root") or self.vault_review.get("base_url")):
            add(self.vault_review, "root", "复盘库未配置，写回复盘不可用（不影响出稿）", "optional")

        # 检索：工作流自己执行（D36）
        if self.retrieval.get("provider") == "http" and not self.retrieval.get("endpoint"):
            add(self.retrieval, "endpoint", "检索通道选了 http 但没填 endpoint", "required")

        # 通知：自建飞书机器人（D48），未就绪时卡片落本地文件
        if self.notify.get("driver") == "feishu_bot":
            for key in ("feishu_app_id", "feishu_app_secret"):
                if not self.notify.get(key):
                    add(self.notify, key, "飞书自建应用凭据未填，闸门卡无法推送", "optional")

        if self.gateway.get("provider") == "http" and not self.gateway.get("api_key"):
            add(self.gateway, "api_key", "模型网关选了 http 但没填 api_key", "required")

        return items

    def capabilities(self) -> dict[str, bool]:
        """当前能做什么。脚手架阶段用来如实报告，而不是假装能跑。"""
        missing = {i["key"] for i in self.doctor_items() if i["kind"] == "required"}
        return {
            "ingest_materials": not any(k.startswith("vault.readonly") for k in missing),
            "retrieval": not any(k.startswith("retrieval") for k in missing),
            "generation": not any(k.startswith("gateway") for k in missing),
            "writeback": not any(k.startswith("vault.review") for k in missing),
            "card_push_feishu": self.notify.get("driver") == "feishu_bot"
            and bool(self.notify.get("feishu_app_id")),
        }


def _section(name: str, values: dict[str, Any], required: tuple[str, ...]) -> Section:
    return Section(name=name, values=dict(values or {}), required=required)


def load(path: str | Path | None = None) -> Config:
    explicit = path or os.environ.get(CONFIG_ENV)
    candidates = [Path(explicit)] if explicit else [Path(p) for p in DEFAULT_PATHS]
    chosen = next((p for p in candidates if p.exists()), None)
    if chosen is None:
        raise ConfigError(
            "找不到配置文件。把 config.example.toml 复制成 config.toml 即可（可先留空，doctor 会列出缺什么）。"
        )
    raw = tomllib.loads(chosen.read_text(encoding="utf-8"))
    return Config(
        path=chosen,
        app=_section("app", raw.get("app", {}), ("name", "data_dir")),
        vault_readonly=_section("vault.readonly", raw.get("vault", {}).get("readonly", {}), ()),
        vault_review=_section("vault.review", raw.get("vault", {}).get("review", {}), ()),
        retrieval=_section("retrieval", raw.get("retrieval", {}), ("provider",)),
        gateway=_section("gateway", raw.get("gateway", {}), ("provider",)),
        notify=_section("notify", raw.get("notify", {}), ("driver",)),
        publish=_section("publish", raw.get("publish", {}), ("mode",)),
    )
