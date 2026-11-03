"""审计：追加写、永不修改（方案"审计留全量"的落地）。

每次 run 的每一步、每张卡片、每个人工答复都进这里，用于：
- 事后追责与复盘（"我本可自己判断"的清单靠它算）；
- 决策台账的安全栏（引用旧决定时必须能回显原话与日期）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Audit:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "audit.jsonl"

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
