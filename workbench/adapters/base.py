"""适配器协议：外部能力全部从这里进，实现可替换（方案 §3 / D45 / D48）。

硬约定（对应方案 §2.4 与 §6）：
- 不可用就抛 AdapterUnavailable，**不许编造内容**；
- 抓不到的内容标 unavailable，不许用模型补写"大概内容"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Note:
    path: str
    title: str
    body_md: str
    frontmatter: dict[str, Any] = field(default_factory=dict)

    @property
    def origin(self) -> str | None:
        """原始出处。空 = 无出处 => 不得进正文断言（D46）。"""
        value = self.frontmatter.get("origin")
        return value or None

    @property
    def has_verifiable_origin(self) -> bool:
        return bool(self.origin)


@dataclass
class ReferenceItem:
    """网络参考：只能用于角度/结构/误区/对比，不能当断言（D32/§6.3）。"""

    url: str
    title: str
    source_kind: str          # docs | blog | forum | video_page | official
    fetched_at: str
    http_status: int
    content_hash: str
    excerpt: str
    key_points: list[str] = field(default_factory=list)
    gap_note: str = ""        # "他漏了什么"
    unavailable: bool = False


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


@runtime_checkable
class VaultRead(Protocol):
    def health(self) -> bool: ...
    def search(self, query: str, *, limit: int = 10) -> list[Note]: ...
    def read(self, path: str) -> Note: ...


@runtime_checkable
class VaultWrite(Protocol):
    def health(self) -> bool: ...
    def write_note(self, rel_path: str, body_md: str, frontmatter: dict[str, Any]) -> str: ...


@runtime_checkable
class Retrieval(Protocol):
    def collect(self, topic: str, seeds: list[str]) -> list[ReferenceItem]: ...


@runtime_checkable
class ModelGateway(Protocol):
    def complete(self, prompt: str, *, tier: str = "standard", schema: dict | None = None) -> Completion: ...


@runtime_checkable
class Notifier(Protocol):
    def send_card(self, card: dict[str, Any]) -> str: ...
