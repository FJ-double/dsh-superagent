"""参考采集（D32/D36）：由工作流自己执行，快照存证必做。

采三类：用法/教程、其他博主同类内容（含"他漏了什么"）、讨论与争议。
抓不到 => unavailable=True，**绝不编造内容**。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .base import ReferenceItem

REFERENCE_USAGE = ("angle", "structure", "pitfall", "comparison")   # 允许的用途
REFERENCE_FORBIDDEN = ("assertion", "pixel")                        # 永远不许


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StubRetrieval:
    """空跑用：返回标了 unavailable 的条目，绝不伪造正文内容。"""

    provider = "stub"

    def collect(self, topic: str, seeds: list[str]) -> list[ReferenceItem]:
        return [
            ReferenceItem(
                url=f"stub://{topic}/{i}",
                title=f"（未配置检索通道，无法抓取：{seed}）",
                source_kind="unavailable",
                fetched_at=_now(),
                http_status=0,
                content_hash="",
                excerpt="",
                unavailable=True,
            )
            for i, seed in enumerate(seeds, start=1)
        ]


class HttpRetrieval:
    """真实检索：搜索 -> 抓正文 -> 落快照 -> 抽要点。

    骨架阶段只实现"抓 + 存证 + 失败如实标注"三段；
    要点抽取留给模型网关（gateway），避免把两件事耦合。
    """

    provider = "http"

    def __init__(self, endpoint: str, snapshot_dir: str | Path, timeout_s: int = 20) -> None:
        self.endpoint = endpoint
        self.snapshot_dir = Path(snapshot_dir)
        self.timeout_s = timeout_s

    def _fetch(self, url: str) -> tuple[int, str]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "workbench/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                return resp.status, resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except (urllib.error.URLError, OSError):
            return 0, ""

    def collect(self, topic: str, seeds: list[str]) -> list[ReferenceItem]:
        items: list[ReferenceItem] = []
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        for seed in seeds:
            status, body = self._fetch(seed if seed.startswith("http") else f"{self.endpoint}{seed}")
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] if body else ""
            if status == 0 or not body:
                items.append(
                    ReferenceItem(
                        url=seed, title="（抓取失败：登录墙/反爬/不可达）",
                        source_kind="unavailable", fetched_at=_now(),
                        http_status=status, content_hash="", excerpt="", unavailable=True,
                    )
                )
                continue
            snapshot = self.snapshot_dir / f"{digest}.html"   # 快照存证：网页会改版会消失
            snapshot.write_text(body, encoding="utf-8")
            items.append(
                ReferenceItem(
                    url=seed, title=topic, source_kind="web", fetched_at=_now(),
                    http_status=status, content_hash=digest,
                    excerpt=body[:800],
                )
            )
        return items
