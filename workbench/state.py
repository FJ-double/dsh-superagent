"""状态机：一个话题（family）扇出成多平台分支（channel）。

不变量（对应方案 §3）：
- 选题与取材共享一次；fanout 之后每条分支独立推进、独立失败；
- 父状态只做聚合：全部完成 / 部分完成 / 全失败——**一条挂不连坐**；
- 已发布的分支不可回退（发布是单向动作）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from .errors import InvariantViolation


class Channel(StrEnum):
    WECHAT = "wechat_mp"
    XHS = "xiaohongshu"
    DOUYIN = "douyin"


class Form(StrEnum):
    ARTICLE = "longform_article"
    CAROUSEL = "carousel"
    VIDEO = "short_video"


FORM_BY_CHANNEL = {
    Channel.WECHAT: Form.ARTICLE,
    Channel.XHS: Form.CAROUSEL,
    Channel.DOUYIN: Form.VIDEO,
}


class Stage(StrEnum):
    """L0–L7 的白话映射见方案 §3.1。fanout 发生在 STORY 之后。"""

    TOPIC = "L1"          # 出题
    INTAKE = "L2a"        # 仓库取材
    REFERENCE = "L2b"     # 网络参考采集
    RECONCILE = "L2c"     # 引用对账（机械，不联网）
    STORY = "L3"          # 母版叙事
    DRAFT = "L3d"         # 三形态成稿（fanout 之后，逐分支）
    LAYOUT = "L4"
    RENDER = "L5"
    QC = "L6"
    PUBLISH = "L7"


SHARED_STAGES = (Stage.TOPIC, Stage.INTAKE, Stage.REFERENCE, Stage.RECONCILE, Stage.STORY)
BRANCH_STAGES = (Stage.DRAFT, Stage.LAYOUT, Stage.RENDER, Stage.QC, Stage.PUBLISH)


class BranchState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_GATE = "awaiting_gate"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"
    MANUAL_UPLOAD = "manual_upload"   # manual 模式交产物，不弹发布卡（D50）
    FAILED = "failed"
    SKIPPED = "skipped"


ALLOWED_TRANSITIONS: dict[BranchState, set[BranchState]] = {
    BranchState.QUEUED: {BranchState.RUNNING, BranchState.SKIPPED},
    BranchState.RUNNING: {
        BranchState.AWAITING_GATE,
        BranchState.READY_TO_PUBLISH,
        BranchState.MANUAL_UPLOAD,
        BranchState.FAILED,
    },
    BranchState.AWAITING_GATE: {BranchState.RUNNING, BranchState.FAILED, BranchState.SKIPPED},
    BranchState.READY_TO_PUBLISH: {BranchState.PUBLISHED, BranchState.FAILED, BranchState.MANUAL_UPLOAD},
    BranchState.MANUAL_UPLOAD: {BranchState.PUBLISHED, BranchState.FAILED},
    BranchState.PUBLISHED: set(),
    BranchState.FAILED: {BranchState.RUNNING, BranchState.SKIPPED},
    BranchState.SKIPPED: set(),
}


@dataclass
class Branch:
    channel: Channel
    state: BranchState = BranchState.QUEUED
    stage: Stage = Stage.DRAFT
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)

    def move(self, to: BranchState, *, reason: str | None = None) -> None:
        if to not in ALLOWED_TRANSITIONS[self.state]:
            raise InvariantViolation(
                f"分支 {self.channel} 不允许从 {self.state} 迁到 {to}"
                + (f"（{reason}）" if reason else "")
            )
        self.state = to


class FamilyState(StrEnum):
    SHARED = "shared"          # 还在共享上游
    FANNED_OUT = "fanned_out"  # 已扇出
    CLOSED = "closed"


@dataclass
class Family:
    family_id: str
    topic: str
    master_form: str          # video_first | article_first
    branches: dict[Channel, Branch] = field(default_factory=dict)
    state: FamilyState = FamilyState.SHARED

    @classmethod
    def open(cls, family_id: str, topic: str, channels: Iterable[Channel], master_form: str) -> "Family":
        fam = cls(family_id=family_id, topic=topic, master_form=master_form)
        for ch in channels:
            fam.branches[Channel(ch)] = Branch(channel=Channel(ch))
        return fam

    def fan_out(self) -> None:
        if self.state is not FamilyState.SHARED:
            raise InvariantViolation("已经扇出过，不要重复扇出")
        if not self.branches:
            raise InvariantViolation("没有分支，扇出无意义")
        self.state = FamilyState.FANNED_OUT

    def aggregate(self) -> str:
        states = {b.state for b in self.branches.values()}
        if states == {BranchState.PUBLISHED}:
            return "all_done"
        if states <= {BranchState.SKIPPED}:
            return "all_skipped"
        if states & {BranchState.PUBLISHED, BranchState.MANUAL_UPLOAD, BranchState.READY_TO_PUBLISH}:
            return "partial"
        return "in_progress"
