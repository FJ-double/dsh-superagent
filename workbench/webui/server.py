"""极简 WebUI 服务：只用标准库，单页 + JSON API。

安全默认（局域网使用的前提）：
1. **绑非 127.0.0.1 必须设口令** —— 否则拒绝启动，避免"内网裸奔批闸门"；
2. 所有 `/api/*` 需要口令（`X-WB-Token` 头或 `?token=`）；
3. 配置只能改**白名单键**，敏感值读出来是掩码，不会被页面回显；
4. 页面本身不含数据，数据全走 API，未认证拿不到任何东西。
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import config as config_mod
from ..audit import Audit
from ..decisions import Ledger
from ..errors import WorkbenchError
from ..gates import Intent, parse_custom_reply
from ..runtime import build_pipeline
from .page import PAGE

SECRET_HINTS = ("token", "secret", "api_key", "password")
WRITABLE_KEYS = {
    "app.data_dir",
    "vault.readonly.driver",
    "vault.readonly.base_url",
    "vault.readonly.token",
    "vault.review.driver",
    "vault.review.root",
    "vault.review.token",
    "retrieval.provider",
    "retrieval.endpoint",
    "gateway.provider",
    "gateway.endpoint",
    "gateway.api_key",
    "notify.driver",
    "notify.feishu_app_id",
    "notify.feishu_app_secret",
    "notify.card_callback_url",
    "publish.mode",
    "webui.host",
    "webui.port",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mask(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return "已设置" if len(text) <= 6 else f"已设置（{text[:3]}…{text[-2:]}）"


@dataclass
class Ctx:
    config_path: Path
    token: str
    allow_run: bool = True
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: dict[str, Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def cfg(self) -> config_mod.Config:
        return config_mod.load(self.config_path)


# ---------- 配置文件行级改写（保留注释与顺序） ----------
def _flatten(cfg: config_mod.Config) -> dict[str, Any]:
    return {
        "app.data_dir": cfg.app.get("data_dir"),
        "vault.readonly.driver": cfg.vault_readonly.get("driver"),
        "vault.readonly.base_url": cfg.vault_readonly.get("base_url"),
        "vault.readonly.token": cfg.vault_readonly.get("token"),
        "vault.review.driver": cfg.vault_review.get("driver"),
        "vault.review.root": cfg.vault_review.get("root"),
        "vault.review.token": cfg.vault_review.get("token"),
        "retrieval.provider": cfg.retrieval.get("provider"),
        "retrieval.endpoint": cfg.retrieval.get("endpoint"),
        "gateway.provider": cfg.gateway.get("provider"),
        "gateway.endpoint": cfg.gateway.get("endpoint"),
        "gateway.api_key": cfg.gateway.get("api_key"),
        "notify.driver": cfg.notify.get("driver"),
        "notify.feishu_app_id": cfg.notify.get("feishu_app_id"),
        "notify.feishu_app_secret": cfg.notify.get("feishu_app_secret"),
        "notify.card_callback_url": cfg.notify.get("card_callback_url"),
        "publish.mode": cfg.publish.get("mode"),
    }


def _section_of(dotted: str) -> tuple[str, str]:
    parts = dotted.split(".")
    return ".".join(parts[:-1]), parts[-1]


def _patch_toml(path: Path, dotted: str, value: str) -> None:
    """保留注释的行级改写：找到 section，替换或插入 key。"""
    section, key = _section_of(dotted)
    header = f"[{section}]"
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_section = False
    written = False
    seen_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not written:          # 段落结束前补写
                out.append(f'{key} = "{value}"')
                written = True
            in_section = stripped == header
            seen_section = seen_section or in_section
            out.append(line)
            continue
        if in_section and re.match(rf"\s*{re.escape(key)}\s*=", line):
            out.append(f'{key} = "{value}"')
            written = True
            continue
        out.append(line)
    if in_section and not written:
        out.append(f'{key} = "{value}"')
        written = True
    if not seen_section:
        out.append("")
        out.append(header)
        out.append(f'{key} = "{value}"')
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    ctx: Ctx
    server_version = "workbench-webui/0.1"

    # ---------- 基础 ----------
    def log_message(self, fmt: str, *args: Any) -> None:   # 静音默认日志
        return

    def _json(self, payload: Any, code: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        if not self.ctx.token:
            return True
        header = self.headers.get("X-WB-Token", "")
        query = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return secrets.compare_digest(header or query, self.ctx.token)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # ---------- 路由 ----------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._html(PAGE)
        if not self._authed():
            return self._json({"error": "需要口令"}, HTTPStatus.UNAUTHORIZED)
        if path == "/api/status":
            return self._json(self._status())
        if path == "/api/config":
            return self._json(self._config_view())
        if path == "/api/cards":
            return self._json({"cards": self._cards()})
        if path == "/api/ledger":
            return self._json({"decisions": [d.__dict__ for d in self._ledger().items]})
        if path == "/api/audit":
            n = int(parse_qs(urlparse(self.path).query).get("n", ["40"])[0])
            return self._json({"events": Audit(self.ctx.cfg.data_dir).tail(n)})
        if path == "/api/runs":
            return self._json({"runs": list(self.ctx.runs.values())[-10:]})
        return self._json({"error": "没有这个接口"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._authed():
            return self._json({"error": "需要口令"}, HTTPStatus.UNAUTHORIZED)
        body = self._body()
        try:
            if path == "/api/config":
                return self._json(self._save_config(body))
            if path == "/api/run":
                return self._json(self._run(body))
            if path == "/api/cards/decide":
                return self._json(self._decide(body))
            if path == "/api/cards/confirm":
                return self._json(self._confirm(body))
            if path == "/api/ledger":
                led = self._ledger()
                d = led.add(body.get("topic", ""), body.get("quote", ""), body.get("conclusion", ""))
                Audit(self.ctx.cfg.data_dir).emit("ledger.add", id=d.id, topic=d.topic)
                return self._json({"ok": True, "decision": d.__dict__})
        except WorkbenchError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:                                  # noqa: BLE001
            return self._json({"error": f"服务端错误：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._json({"error": "没有这个接口"}, HTTPStatus.NOT_FOUND)

    # ---------- 实现 ----------
    def _ledger(self) -> Ledger:
        return Ledger(self.ctx.cfg.data_dir / "decisions.json")

    def _status(self) -> dict[str, Any]:
        cfg = self.ctx.cfg
        return {
            "at": _now(),
            "config_path": str(cfg.path),
            "items": cfg.doctor_items(),
            "capabilities": cfg.capabilities(),
            "mode": cfg.publish.get("mode"),
            "platforms": cfg.publish.get("platforms", []),
            "allow_run": self.ctx.allow_run,
        }

    def _config_view(self) -> dict[str, Any]:
        flat = _flatten(self.ctx.cfg)
        rows = []
        for key in sorted(WRITABLE_KEYS):
            if key.startswith("webui."):
                continue
            value = flat.get(key, "")
            secret = any(h in key for h in SECRET_HINTS)
            rows.append(
                {
                    "key": key,
                    "value": "" if secret and not value else (_mask(value) if secret else value),
                    "secret": secret,
                    "filled": bool(value),
                    "writable": key in WRITABLE_KEYS,
                }
            )
        return {"rows": rows}

    def _save_config(self, body: dict[str, Any]) -> dict[str, Any]:
        key = str(body.get("key", ""))
        if key not in WRITABLE_KEYS:
            raise WorkbenchError(f"{key} 不在可写白名单里，页面不能改这一项")
        _patch_toml(self.ctx.config_path, key, str(body.get("value", "")))
        Audit(self.ctx.cfg.data_dir).emit("config.update", key=key)
        return {"ok": True, "key": key}

    def _run(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.ctx.allow_run:
            raise WorkbenchError("服务端禁止从页面触发运行（webui.allow_run=false）")
        topic = str(body.get("topic", "")).strip()
        if not topic:
            raise WorkbenchError("先说清要做什么题")
        run_id = f"run-{secrets.token_hex(4)}"
        self.ctx.runs[run_id] = {"run_id": run_id, "topic": topic, "state": "running", "at": _now()}

        def work() -> None:
            try:
                cfg = self.ctx.cfg
                pipe = build_pipeline(cfg, with_vault=bool(body.get("with_vault", True)))
                result = pipe.run(topic, master_form=str(body.get("master_form", "article_first")))
                self.ctx.runs[run_id].update(
                    state="done",
                    family=result.family.family_id,
                    aggregate=result.family.aggregate(),
                    cards=result.card_paths,
                    notes=result.notes,
                    branches={
                        str(ch): {"state": str(br.state), "artifacts": br.artifacts}
                        for ch, br in result.family.branches.items()
                    },
                    finished_at=_now(),
                )
            except Exception as exc:                              # noqa: BLE001
                self.ctx.runs[run_id].update(state="failed", error=str(exc), finished_at=_now())

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "run_id": run_id}

    def _cards(self) -> list[dict[str, Any]]:
        card_dir = Path(self.ctx.cfg.notify.get("out_dir", "./.data/cards"))
        if not card_dir.is_dir():
            return []
        cards = []
        for path in sorted(card_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            cards.append(
                {
                    "gate_id": payload.get("gate_id", path.stem),
                    "gate": payload.get("gate"),
                    "channel": payload.get("channel"),
                    "ask": payload.get("ask"),
                    "recommendation": payload.get("recommendation"),
                    "confidence": payload.get("confidence"),
                    "reasons": payload.get("reasons", []),
                    "risk": payload.get("risk"),
                    "question": payload.get("question"),
                    "alternatives": payload.get("alternatives", []),
                    "buttons": payload.get("buttons", []),
                    "at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                }
            )
        return cards

    def _find_card(self, gate_id: str) -> dict[str, Any]:
        for card in self._cards():
            if card["gate_id"] == gate_id:
                return card
        raise WorkbenchError(f"找不到卡片 {gate_id}（可能已被处理或目录不对）")

    def _decide(self, body: dict[str, Any]) -> dict[str, Any]:
        gate_id = str(body.get("gate_id", ""))
        action = str(body.get("action", ""))
        card = self._find_card(gate_id)
        audit = Audit(self.ctx.cfg.data_dir)

        if action in ("approve", "reject"):
            self._record_decision(gate_id, card, action, raw="")
            return {"ok": True, "applied": True, "decision": action}

        if action != "custom":
            raise WorkbenchError("只能同意、拒绝或自定义回复")
        receipt = parse_custom_reply(str(body.get("text", "")), confidence=float(body.get("confidence", 0.75)))
        rid = f"rcp-{secrets.token_hex(3)}"
        self.ctx.pending[rid] = {"gate_id": gate_id, "receipt": receipt, "card": card}
        audit.emit("gate.custom_receipt", gate_id=gate_id, intent=str(receipt.intent),
                   echo=receipt.echo, confirmed=False)
        return {
            "ok": True,
            "applied": False,
            "receipt_id": rid,
            "intent": str(receipt.intent),
            "echo": receipt.echo,
            "needs_confirm": receipt.intent is not Intent.UNPARSED,
        }

    def _confirm(self, body: dict[str, Any]) -> dict[str, Any]:
        rid = str(body.get("receipt_id", ""))
        pending = self.ctx.pending.get(rid)
        if not pending:
            raise WorkbenchError("这条回执已失效，请重新提交你的回复")
        if not body.get("confirmed"):
            self.ctx.pending.pop(rid, None)
            Audit(self.ctx.cfg.data_dir).emit("gate.custom_cancelled", receipt=rid)
            return {"ok": True, "applied": False, "note": "已作废，未执行任何动作"}
        self.ctx.pending.pop(rid, None)
        self._record_decision(pending["gate_id"], pending["card"],
                              f"custom:{pending['receipt'].intent}",
                              raw=pending["receipt"].raw_text)
        return {"ok": True, "applied": True, "decision": pending["receipt"].intent}

    def _record_decision(self, gate_id: str, card: dict[str, Any], decision: str, raw: str) -> None:
        ledger = self._ledger()
        ledger.add(
            topic=f"gate:{gate_id}",
            quote=raw or decision,
            conclusion=f"{card.get('ask', '')} → {decision}",
            conditions={"gate": str(card.get("gate")), "channel": str(card.get("channel"))},
        )
        Audit(self.ctx.cfg.data_dir).emit("gate.decided", gate_id=gate_id, decision=decision,
                                          raw=raw, ask=card.get("ask"))


def serve(config_path: str | Path | None = None, *, host: str | None = None,
          port: int | None = None, token: str | None = None, allow_run: bool | None = None) -> None:
    import os

    cfg_path = Path(config_path or os.environ.get(config_mod.CONFIG_ENV) or "config.toml")
    cfg = config_mod.load(cfg_path)
    bind_host = host or str(cfg.notify.get("web_host", "") or _webui(cfg, "host", "127.0.0.1"))
    bind_port = int(port or _webui(cfg, "port", 8787))
    auth_token = token if token is not None else (
        os.environ.get("WB_WEB_TOKEN") or str(_webui(cfg, "token", "") or "")
    )
    if bind_host not in ("127.0.0.1", "localhost") and not auth_token:
        raise WorkbenchError(
            "绑定到局域网地址必须先设口令：在 config.toml 的 [webui] 里设 token，或设置环境变量 WB_WEB_TOKEN。\n"
            "理由：闸门卡片上有\"同意发布\"这种按钮，内网裸奔等于谁都能替你拍板。"
        )
    Handler.ctx = Ctx(
        config_path=cfg_path,
        token=auth_token,
        allow_run=bool(allow_run if allow_run is not None else _webui(cfg, "allow_run", True)),
    )
    httpd = ThreadingHTTPServer((bind_host, bind_port), Handler)
    print(f"工作台 WebUI 已启动，监听 {bind_host}:{bind_port}")
    for url in _access_urls(bind_host, bind_port):
        print(f"  打开：{url}")
    if auth_token:
        print("需要口令：页面首次打开会问；也可以直接访问 http://<ip>:<port>/?token=<口令>")
    print("Ctrl-C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


def _access_urls(host: str, port: int) -> list[str]:
    """列出这台机器上可以打开页面的地址，省得用户去查 IP。"""
    import socket

    if host not in ("0.0.0.0", ""):
        return [f"http://{host}:{port}/"]
    urls = [f"http://127.0.0.1:{port}/"]
    try:
        shown = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        shown = []
    for ip in shown:
        if not ip.startswith("127."):
            urls.append(f"http://{ip}:{port}/")
    if len(urls) == 1:                      # 容器里常见：拿不到局域网 IP
        urls.append(f"http://<这台机器的局域网IP>:{port}/")
    return urls


def _webui(cfg: config_mod.Config, key: str, default: Any) -> Any:
    raw = Path(cfg.path).read_text(encoding="utf-8")
    data = tomllib.loads(raw).get("webui", {})
    value = data.get(key, default)
    return default if value == "" else value
