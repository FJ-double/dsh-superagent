"""编排：共享上游 -> 扇出 -> 分支独立推进（方案 §3.2/§3.4）。

写法上的两个硬约束：
1. 扇出之前不许有任何分支动作（否则就退化成"三条流水线从头并发"）；
2. 分支失败只标自己，父状态取聚合，绝不连坐。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .adapters.base import ModelGateway, Notifier, Retrieval, VaultRead, VaultWrite
from .audit import Audit
from .gates import Card, Gate
from .qc import QCReport, attribution_redline, reconcile_citations, reference_boundary, style_pass
from .state import BranchState, Channel, Family, FORM_BY_CHANNEL, Stage
from .store import ArtifactStore


@dataclass
class RunResult:
    family: Family
    artifacts: dict[str, str]
    card_paths: list[str]
    qc: dict[str, QCReport]
    notes: list[str]


class Pipeline:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        audit: Audit,
        vault_read: VaultRead | None = None,
        vault_write: VaultWrite | None = None,
        retrieval: Retrieval | None = None,
        gateway: ModelGateway | None = None,
        notifier: Notifier | None = None,
        publish_mode: str = "manual",
        platforms: tuple[Channel, ...] = (Channel.WECHAT, Channel.XHS),
        forbidden_cliches: tuple[str, ...] = (),
    ) -> None:
        self.store = store
        self.audit = audit
        self.vault_read = vault_read
        self.vault_write = vault_write
        self.retrieval = retrieval
        self.gateway = gateway
        self.notifier = notifier
        self.publish_mode = publish_mode
        self.platforms = platforms
        self.forbidden_cliches = list(forbidden_cliches)

    # ---------- 共享上游 ----------
    def _shared(self, family: Family) -> tuple[dict[str, Any], list[str]]:
        shared: dict[str, Any] = {}
        notes: list[str] = []

        # L2a 取材：仓库只读。不可用就如实降级为"无素材"，不编造
        materials: list[dict[str, Any]] = []
        if self.vault_read is not None:
            try:
                for note in self.vault_read.search(family.topic, limit=5):
                    materials.append(
                        {
                            "path": note.path,
                            "title": note.title,
                            "origin": note.origin,
                            "can_assert": note.has_verifiable_origin,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"取材不可用：{exc}")
                self.audit.emit("intake.unavailable", family=family.family_id, error=str(exc))
        shared["materials"] = materials
        self.audit.emit("stage.done", family=family.family_id, stage=Stage.INTAKE,
                        count=len(materials))

        # L2b 参考采集：工作流自己做，抓不到就标 unavailable
        refs = []
        if self.retrieval is not None:
            try:
                refs = self.retrieval.collect(family.topic, [n["title"] for n in materials] or [family.topic])
            except Exception as exc:  # noqa: BLE001
                notes.append(f"参考采集失败：{exc}")
        shared["references"] = [r.__dict__ for r in refs]
        unavailable = sum(1 for r in refs if r.unavailable)
        if unavailable:
            notes.append(f"参考采集：{unavailable}/{len(refs)} 条抓不到（登录墙/反爬/未配置），已如实标注")
        self.audit.emit("stage.done", family=family.family_id, stage=Stage.REFERENCE,
                        total=len(refs), unavailable=unavailable)

        # L3 母版叙事：论点只有一份，子形态只能删不能加
        narrative = {
            "narrative_id": f"narr-{uuid.uuid4().hex[:8]}",
            "topic": family.topic,
            "master_form": family.master_form,
            "thesis": f"[骨架占位] 关于「{family.topic}」的核心论点（待模型接入后生成）",
            "claims": [],
        }
        if self.gateway is not None:
            narrative["thesis_model_note"] = self.gateway.complete(
                f"为话题「{family.topic}」写一句核心论点", tier="standard"
            ).text
        shared["narrative"] = narrative
        self.audit.emit("stage.done", family=family.family_id, stage=Stage.STORY)
        return shared, notes

    # ---------- 分支 ----------
    def _branch(self, family: Family, channel: Channel, shared: dict[str, Any]) -> tuple[dict, QCReport]:
        form = FORM_BY_CHANNEL[channel]
        draft = f"[骨架占位] {channel} / {form} 的正文（母版：{shared['narrative']['narrative_id']}）"
        qc = QCReport(
            findings=[
                reconcile_citations(draft, []),
                reference_boundary(draft, []),
                attribution_redline(draft),
                *style_pass(draft, self.forbidden_cliches),
            ]
        )
        return {"channel": str(channel), "form": str(form), "draft": draft,
                "narrative_id": shared["narrative"]["narrative_id"], "verdict": qc.verdict}, qc

    # ---------- 主流程 ----------
    def run(self, topic: str, *, master_form: str = "article_first") -> RunResult:
        family = Family.open(
            family_id=f"fam-{uuid.uuid4().hex[:8]}",
            topic=topic,
            channels=self.platforms,
            master_form=master_form,
        )
        self.audit.emit("family.open", family=family.family_id, topic=topic,
                        channels=[str(c) for c in self.platforms], mode=self.publish_mode)

        shared, notes = self._shared(family)

        # G1 选题卡（票拟）
        g1 = Card(
            gate=Gate.TOPIC,
            ask=f"建议做「{topic}」，共 {len(self.platforms)} 个平台分支",
            recommendation="approve",
            confidence="中",
            reasons=[
                f"仓库有料：{len(shared['materials'])} 条（其中可进断言 {sum(1 for m in shared['materials'] if m['can_assert'])} 条）",
                f"参考已采：{sum(1 for r in shared['references'] if not r['unavailable'])} 条可用",
                "同角度近 90 天未发（数据脚暂空，冷启动期不假装数据驱动）",
            ],
            metrics={"本话题分平台成本": "待模型接入后按实际记账", "数据表现": "数据不足（冷启动）"},
            risk="最坏情况是选题与已有内容重合、互动偏低",
            not_worried="角度来自母版叙事而非模板套用；重合可直接否决重出",
            done=["仓库检索", "参考采集", "母版叙事"],
            todo=["等你在 G1 批红后才扇出"],
            alternatives=["换角度重出", "先补料再开工"],
            question="按这个方向开工，还是换角度？",
            footer={"family": family.family_id, "mode": self.publish_mode, "第几张": 1},
        )
        card_paths = [self._deliver_card(g1, f"{family.family_id}-G1")]

        shared["g1_card"] = g1.to_payload(f"{family.family_id}-G1")
        art = self.store.put("narrative", shared["narrative"], meta={"family": family.family_id})
        self.audit.emit("artifact.put", artifact=art.artifact_id, kind="narrative")

        # 扇出
        family.fan_out()
        self.audit.emit("family.fanout", family=family.family_id)
        artifacts = {"narrative": art.artifact_id}
        qcs: dict[str, QCReport] = {}

        for channel, branch in family.branches.items():
            branch.move(BranchState.RUNNING)
            try:
                payload, qc = self._branch(family, channel, shared)
                a = self.store.put(f"draft/{channel}", payload,
                                   meta={"family": family.family_id, "verdict": qc.verdict})
                branch.artifacts.append(a.artifact_id)
                artifacts[str(channel)] = a.artifact_id
                qcs[str(channel)] = qc
                branch.move(
                    BranchState.FAILED if qc.verdict != "pass" else
                    (BranchState.MANUAL_UPLOAD if self.publish_mode == "manual" else BranchState.READY_TO_PUBLISH)
                )
            except Exception as exc:  # noqa: BLE001
                branch.error = str(exc)
                branch.move(BranchState.FAILED)
                notes.append(f"{channel} 分支失败：{exc}")
                self.audit.emit("branch.failed", family=family.family_id,
                                channel=str(channel), error=str(exc))

        self.audit.emit("family.aggregate", family=family.family_id, state=family.aggregate())
        return RunResult(family=family, artifacts=artifacts, card_paths=card_paths,
                         qc=qcs, notes=notes)

    def _deliver_card(self, card: Card, gate_id: str) -> str:
        payload = card.to_payload(gate_id)
        if self.notifier is None:
            return "<no notifier>"
        try:
            return self.notifier.send_card(payload)
        except Exception as exc:  # noqa: BLE001
            # 发不出去就落本地，绝不假装已推送（D48）
            from .adapters.notify import FileNotifier
            from pathlib import Path

            local = FileNotifier(Path(self.store.root).parent / "cards")
            path = local.send_card(payload)
            self.audit.emit("card.delivery_fallback", gate_id=gate_id, error=str(exc), path=path)
            return path
