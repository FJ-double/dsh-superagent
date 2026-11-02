"""产物仓库：内容寻址 + 不可变 + 可 diff（方案 §3 的"数据面"）。

只做本地文件系统实现；以后换对象存储只改这个文件。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Artifact:
    artifact_id: str      # 内容哈希（sha256 前 16 位）
    kind: str
    rel_path: str
    created_at: str
    meta: dict[str, Any]


class ArtifactStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(Path(data_dir) / "index.sqlite")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS artifacts(
                   artifact_id TEXT PRIMARY KEY,
                   kind TEXT, rel_path TEXT, created_at TEXT, meta TEXT)"""
        )
        self._db.commit()

    def put(self, kind: str, payload: Any, *, meta: dict[str, Any] | None = None) -> Artifact:
        blob = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()[:16]
        sub = self.root / kind
        sub.mkdir(parents=True, exist_ok=True)
        rel = f"{kind}/{digest}.json" if not isinstance(payload, bytes) else f"{kind}/{digest}.bin"
        path = self.root / rel
        if not path.exists():                      # 内容寻址 => 同内容不重复写
            path.write_bytes(blob)
        art = Artifact(digest, kind, rel, _now(), meta or {})
        self._db.execute(
            "REPLACE INTO artifacts VALUES (?,?,?,?,?)",
            (art.artifact_id, art.kind, art.rel_path, art.created_at,
             json.dumps(art.meta, ensure_ascii=False)),
        )
        self._db.commit()
        return art

    def get(self, artifact_id: str) -> Any:
        row = self._db.execute(
            "SELECT rel_path FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"没有这个产物：{artifact_id}")
        return json.loads((self.root / row[0]).read_text(encoding="utf-8"))

    def by_kind(self, kind: str) -> list[Artifact]:
        rows = self._db.execute(
            "SELECT artifact_id, kind, rel_path, created_at, meta FROM artifacts "
            "WHERE kind=? ORDER BY created_at",
            (kind,),
        ).fetchall()
        return [Artifact(r[0], r[1], r[2], r[3], json.loads(r[4])) for r in rows]
