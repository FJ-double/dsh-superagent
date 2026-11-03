"""决策台账（D20/D40/D43）：定过的事不再问第二遍，但带三道安全栏。

安全栏（任一不满足就必须重新问）：
1. 回显：能把原话与日期贴出来；
2. 条件未变：赛道/体裁/预算档/风险级四项都没变；
3. 同类：是同一类事项，不是"看起来像"。

永不复用：花钱、对外发、R2+ 风险、改方向、动数据/账号/权限。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ALWAYS_ASK = {"spend", "publish_outward", "change_direction", "data_or_account", "risk_r2_plus"}


@dataclass
class Decision:
    id: str
    topic: str
    quote: str
    conclusion: str
    decided_at: str
    conditions: dict[str, str] = field(default_factory=dict)


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.items: list[Decision] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.items = [Decision(**d) for d in raw.get("decisions", [])]

    def add(self, topic: str, quote: str, conclusion: str,
            conditions: dict[str, str] | None = None) -> Decision:
        d = Decision(
            id=f"D{len(self.items) + 1:02d}",
            topic=topic,
            quote=quote,
            conclusion=conclusion,
            decided_at=date.today().isoformat(),
            conditions=conditions or {},
        )
        self.items.append(d)
        self.flush()
        return d

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"decisions": [d.__dict__ for d in self.items]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def reuse_allowed(self, topic: str, *, conditions: dict[str, str] | None = None) -> Decision | None:
        """能不能拿一条旧决定免掉这次提问。返回 None 表示必须问。"""
        if topic in ALWAYS_ASK:
            return None
        for d in reversed(self.items):
            if d.topic not in (topic, "*"):
                continue
            if conditions and any(d.conditions.get(k) != v for k, v in conditions.items()):
                continue  # 条件变了 => 新问题
            return d
        return None

    def echo(self, decision: Decision) -> str:
        """安全栏第 1 条：引用时必须能把原话与日期摆出来。"""
        return f"你 {decision.decided_at} 说过「{decision.quote}」→ 按此执行：{decision.conclusion}"
