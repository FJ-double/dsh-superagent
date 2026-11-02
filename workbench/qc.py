"""质检：硬错层拦发布 / 风格层只记账（D19）。外加两条本项目特有的检查。

硬错层：引用对账（D37）、参考越界（§6.3）、点名红线（D38）、结构完整、音画同步占位
风格层：去陈词、AI 味、套话密度 —— 只进分数，不拦
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .adapters.base import Note, ReferenceItem


class Layer(StrEnum):
    HARD = "hard"      # 拦发布
    STYLE = "style"    # 不拦


@dataclass
class Finding:
    check: str
    layer: Layer
    passed: bool
    detail: str


@dataclass
class QCReport:
    findings: list[Finding]

    @property
    def hard_failures(self) -> list[Finding]:
        return [f for f in self.findings if f.layer is Layer.HARD and not f.passed]

    @property
    def style_notes(self) -> list[Finding]:
        return [f for f in self.findings if f.layer is Layer.STYLE]

    @property
    def verdict(self) -> str:
        return "pass" if not self.hard_failures else "revise"

    def score(self) -> int:
        penalty = sum(4 for f in self.style_notes if not f.passed)
        return max(0, 100 - penalty - 20 * len(self.hard_failures))


# 只认"量"（价格、倍数、字数、时长等），不认标识符里的数字：
# "narr-5c03a954" 这种前后贴着字母/连字符的，不算数字断言。
NUMBER = re.compile(r"(?<![\w-])\d+(?:\.\d+)?(?![\w-])")


def reconcile_citations(draft_text: str, notes: list[Note]) -> Finding:
    """引用对账（D37）：不判事实，只查"抄进稿子时有没有抄歪"。

    规则：稿子里出现的数字，必须在所引笔记原文里出现过；笔记里带
    "推测/听说/据说/可能" 的段落，不许在稿子里被写成"数据显示/实测"。
    """
    if not notes:
        return Finding(
            "引用对账", Layer.HARD, True,
            "无素材可对账（对账基准=笔记原文；真实生产时这里必须有素材，否则说明取材没做）",
        )
    note_text = "\n".join(n.body_md for n in notes)
    drifted = [n for n in NUMBER.findall(draft_text) if n not in note_text]
    if drifted:
        return Finding(
            "引用对账-数字漂移", Layer.HARD, False,
            f"稿子里有 {len(drifted)} 个数字在笔记原文里找不到：{drifted[:5]}",
        )
    hedged = ("推测", "听说", "据说", "可能", "估计")
    asserted = re.findall(r"(?:数据显示|实测证明|统计表明)", draft_text)
    if any(h in note_text for h in hedged) and asserted:
        return Finding(
            "引用对账-语气升格", Layer.HARD, False,
            f"笔记里是推测口吻，稿子里出现了「{asserted[0]}」，属于把猜测写成结论",
        )
    return Finding("引用对账", Layer.HARD, True, "数字与语气均与笔记原文一致")


def reference_boundary(draft_text: str, refs: list[ReferenceItem]) -> Finding:
    """参考越界检查：参考内容不得进正文断言（§6.3）。"""
    leaked = [r for r in refs if not r.unavailable and r.url and r.url in draft_text]
    if leaked:
        return Finding(
            "参考越界", Layer.HARD, False,
            f"正文里直接出现了参考来源链接（{len(leaked)} 处），参考只能做角度/结构参照",
        )
    return Finding("参考越界", Layer.HARD, True, "参考内容未进入正文断言")


def attribution_redline(draft_text: str) -> Finding:
    """点名红线（D38）：不许点名做负面指控。命中即拦，交人判断。"""
    negative = ("恰饭", "水文", "割韭菜", "洗稿", "骗钱")
    hit = [w for w in negative if w in draft_text]
    if hit:
        return Finding(
            "点名红线", Layer.HARD, False,
            f"出现对具体对象的负面指控词：{hit}；这类必须走人工判断（对外发 + 高风险）",
        )
    return Finding("点名红线", Layer.HARD, True, "未发现对具体对象的负面指控")


def style_pass(draft_text: str, forbidden_cliches: list[str]) -> list[Finding]:
    """风格层：只记账，不拦发布。"""
    hits = [c for c in forbidden_cliches if c and c in draft_text]
    repeat = re.findall(r"(.{6,})\1", draft_text)
    return [
        Finding("去陈词", Layer.STYLE, not hits, f"命中陈词：{hits}" if hits else "无陈词命中"),
        Finding("句式重复", Layer.STYLE, not repeat, f"重复片段 {len(repeat)} 处"),
    ]
