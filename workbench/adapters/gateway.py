"""模型网关：统一入口 + token 记账（D47：不设上限，只记账）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import Completion


@dataclass
class UsageLedger:
    """每条任务的消耗账。异常高只提示、不拦。"""

    anomaly_multiplier: float = 3.0
    history: list[int] = field(default_factory=list)

    def record(self, tokens: int) -> str | None:
        note = None
        if self.history:
            median = sorted(self.history)[len(self.history) // 2]
            if median and tokens >= median * self.anomaly_multiplier:
                note = f"本条消耗 {tokens} tokens，约为同类中位数的 {tokens / median:.1f} 倍（仅提示，不拦截）"
        self.history.append(tokens)
        return note


class StubGateway:
    provider = "stub"

    def __init__(self, usage: UsageLedger | None = None) -> None:
        self.usage = usage or UsageLedger()
        self.notes: list[str] = []

    def complete(self, prompt: str, *, tier: str = "standard", schema: dict | None = None) -> Completion:
        text = "[stub] 未配置模型通道，此处只返回占位文本，绝不冒充真实生成结果。"
        comp = Completion(text=text, tokens_in=len(prompt) // 4, tokens_out=len(text) // 4, model=f"stub:{tier}")
        note = self.usage.record(comp.tokens_in + comp.tokens_out)
        if note:
            self.notes.append(note)
        return comp
