"""闸门卡片：票拟式（D17），九字段 + 三按钮，自定义回复必须先回执确认。

自检（写进代码里）：卡片没有 `recommendation` 就是错的——票拟里没有票拟，
等于把决策推回给用户。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .errors import InvariantViolation

MAX_ASK_CHARS = 30


class Gate(StrEnum):
    TOPIC = "G1"
    DRAFT = "G2"
    PUBLISH = "G3"
    ANOMALY = "GX"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CUSTOM = "custom"


class Intent(StrEnum):
    REDIRECT = "redirect"   # 改方向
    TUNE = "tune"           # 改参数
    ANSWER = "answer"       # 只是答复
    UNPARSED = "unparsed"   # 解析不了 -> 必须回问，不动状态


@dataclass
class Card:
    gate: Gate
    ask: str
    recommendation: str          # approve | reject | rerun | escalate
    confidence: str              # 高 / 中 / 低
    reasons: list[str]
    metrics: dict[str, Any]
    risk: str
    not_worried: str
    done: list[str]
    todo: list[str]
    alternatives: list[str]
    question: str
    footer: dict[str, Any] = field(default_factory=dict)
    channel: str | None = None   # 每平台一张卡（D27），不合并

    def __post_init__(self) -> None:
        if len(self.ask) > MAX_ASK_CHARS:
            raise InvariantViolation(f"一句话请示超过 {MAX_ASK_CHARS} 字：{self.ask}")
        if not self.recommendation:
            raise InvariantViolation("卡片必须带建议（票拟），否则等于把决策推回给用户")
        if len(self.reasons) > 3:
            raise InvariantViolation("理由最多 3 条，多了没人看")
        if len(self.alternatives) > 2:
            raise InvariantViolation("备选最多 2 个")

    def to_payload(self, gate_id: str) -> dict[str, Any]:
        return {
            "gate_id": gate_id,
            "gate": str(self.gate),
            "channel": self.channel,
            "ask": self.ask,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "metrics": self.metrics,
            "risk": self.risk,
            "not_worried": self.not_worried,
            "done": self.done,
            "todo": self.todo,
            "alternatives": self.alternatives,
            "question": self.question,
            "footer": self.footer,
            "buttons": ["同意", "拒绝", "自定义回复"],
        }


@dataclass
class Receipt:
    """自定义回复的回执：没有 confirmed=True 不许执行（D17）。"""

    raw_text: str
    intent: Intent
    echo: str
    confirmed: bool = False


def parse_custom_reply(text: str, *, confidence: float = 0.0) -> Receipt:
    """只归三类；置信度低一律 unparsed，绝不擅自执行。"""
    if confidence < 0.6 or not text.strip():
        return Receipt(
            raw_text=text,
            intent=Intent.UNPARSED,
            echo="我没懂你的意思。你要的是：① 改方向 ② 改参数 ③ 只是答复？",
        )
    lowered = text.lower()
    if any(k in lowered for k in ("换成", "换个", "角度", "方向", "重写")):
        intent, action = Intent.REDIRECT, "从受影响的最上游一层局部重跑"
    elif any(k in lowered for k in ("标题", "封面", "预算", "字数", "改成")):
        intent, action = Intent.TUNE, "只改对应字段并重跑该层及下游校验"
    else:
        intent, action = Intent.ANSWER, "作为本次决策依据，然后重新挂起等你批"
    return Receipt(raw_text=text, intent=intent, echo=f"我理解你要我做的是「{text}」，我将{action}。对吗？")
