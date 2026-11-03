"""闸门卡片投递（D48）。

- `file`：写本地 JSON（机器人没就绪时的兜底）
- `feishu_bot`：自建飞书机器人（发卡 + 卡片回调）；凭据缺失时如实报错，不静默丢卡
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import AdapterUnavailable


class FileNotifier:
    driver = "file"

    def __init__(self, out_dir: str | Path) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.sent: list[Path] = []

    def send_card(self, card: dict[str, Any]) -> str:
        gate_id = card.get("gate_id", "card")
        path = self.out_dir / f"{gate_id}.json"
        path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        self.sent.append(path)
        return str(path)


class FeishuBotNotifier:
    """自建应用：发互动卡片，并接受卡片回调（回调本体在服务层，这里只发）。

    未配置时抛 AdapterUnavailable —— 调用方应把卡落本地，而不是假装发出去了。
    """

    driver = "feishu_bot"

    def __init__(self, app_id: str, app_secret: str, open_id: str, callback_url: str = "") -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.open_id = open_id
        self.callback_url = callback_url

    def _token(self) -> str:
        import json as _json
        import urllib.request

        if not (self.app_id and self.app_secret):
            raise AdapterUnavailable("飞书自建应用凭据未配置（app_id/app_secret）")
        payload = _json.dumps(
            {"app_id": self.app_id, "app_secret": self.app_secret}
        ).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return _json.loads(resp.read())["tenant_access_token"]
        except Exception as exc:  # noqa: BLE001
            raise AdapterUnavailable(f"取飞书 tenant token 失败：{exc}") from exc

    def send_card(self, card: dict[str, Any]) -> str:
        token = self._token()
        import json as _json
        import urllib.request

        body = _json.dumps(
            {
                "receive_id": self.open_id,
                "msg_type": "interactive",
                "content": _json.dumps(card, ensure_ascii=False),
            }
        ).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return _json.loads(resp.read()).get("data", {}).get("message_id", "")
