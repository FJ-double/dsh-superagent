"""Obsidian 仓库适配器（双库，D44）。

- 素材库：只读，`http_rest`（Local REST API 插件）或 `fs`
- 复盘库：读写，`fs` 或 `http_rest`；系统唯一写入口，只增不改、`[系统]` 前缀
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from ..errors import AdapterUnavailable
from .base import Note


class FsVaultRead:
    """本地目录只读实现：开发和测试用，也是"素材库挂载成本地目录"的走法。"""

    driver = "fs"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def health(self) -> bool:
        return self.root.is_dir()

    def _iter_notes(self):
        if not self.health():
            raise AdapterUnavailable(f"素材库目录不存在：{self.root}")
        for path in self.root.rglob("*.md"):
            yield path

    def search(self, query: str, *, limit: int = 10) -> list[Note]:
        hits: list[Note] = []
        q = query.lower()
        for path in self._iter_notes():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if q in text.lower() or q in path.name.lower():
                hits.append(self._note(path, text))
        return hits[:limit]

    def read(self, rel_path: str) -> Note:
        path = self.root / rel_path
        if not path.exists():
            raise AdapterUnavailable(f"笔记不存在：{rel_path}")
        return self._note(path, path.read_text(encoding="utf-8"))

    @staticmethod
    def _note(path: Path, text: str) -> Note:
        frontmatter: dict[str, Any] = {}
        body = text
        if text.startswith("---"):
            _, fm, body = text.split("---", 2)
            for line in fm.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = v.strip()
        return Note(path=str(path.name), title=frontmatter.get("title", path.stem),
                    body_md=body.strip(), frontmatter=frontmatter)


class HttpRestVaultRead:
    """Obsidian Local REST API 只读客户端。

    骨架阶段只做真实请求的构造与错误映射，**不静默降级**：
    连不上 => AdapterUnavailable，让上层如实报告"取材不可用"。
    """

    driver = "http_rest"

    def __init__(self, base_url: str, token: str = "", verify_tls: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_tls = verify_tls

    def _request(self, path: str) -> Any:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(f"{self.base_url}{path}")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise AdapterUnavailable(f"素材库不可达：{self.base_url}（{exc}）") from exc

    def health(self) -> bool:
        try:
            self._request("/")                       # 插件根路径返回服务信息
            return True
        except AdapterUnavailable:
            return False

    def search(self, query: str, *, limit: int = 10) -> list[Note]:
        data = self._request(f"/search/simple/?query={query}&contextLength=120")
        notes = []
        for item in data if isinstance(data, list) else data.get("results", []):
            notes.append(
                Note(
                    path=item.get("filename", ""),
                    title=item.get("filename", "").rsplit("/", 1)[-1].rstrip(".md"),
                    body_md=item.get("context", ""),
                    frontmatter=item.get("frontMatter", {}) or {},
                )
            )
        return notes[:limit]

    def read(self, rel_path: str) -> Note:
        text = self._request(f"/vault/{rel_path}")
        return FsVaultRead._note(Path(rel_path), json.dumps(text) if not isinstance(text, str) else text)


class FsVaultWrite:
    """复盘库写入：只增不改、`[系统]` 前缀、落 `系统复盘/` 目录（§2.5）。"""

    driver = "fs"
    SUBDIR = "系统复盘"
    PREFIX = "[系统]"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def health(self) -> bool:
        return self.root.is_dir() and os.access(self.root, os.W_OK)

    def write_note(self, rel_path: str, body_md: str, frontmatter: dict[str, Any]) -> str:
        if not self.health():
            raise AdapterUnavailable(f"复盘库不可写：{self.root}")
        target = self.root / self.SUBDIR / rel_path
        if not target.name.startswith(self.PREFIX):
            target = target.with_name(f"{self.PREFIX} {date.today().isoformat()} {target.name}")
        if target.exists():                          # 只增不改
            raise AdapterUnavailable(f"复盘库已存在同名文件，拒绝覆盖：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
        target.write_text(f"---\n{fm}\n---\n\n{body_md}\n", encoding="utf-8")
        return str(target.relative_to(self.root))
