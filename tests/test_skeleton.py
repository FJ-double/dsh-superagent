"""骨架的契约测试：跑的是"规则"，不是"外部能力"。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench import config as config_mod
from workbench.adapters.base import Note, ReferenceItem
from workbench.adapters.gateway import StubGateway, UsageLedger
from workbench.adapters.notify import FileNotifier
from workbench.adapters.retrieval import StubRetrieval
from workbench.audit import Audit
from workbench.decisions import Ledger
from workbench.errors import InvariantViolation
from workbench.gates import Card, Decision, Gate, Intent, parse_custom_reply
from workbench.pipeline import Pipeline
from workbench.qc import attribution_redline, reconcile_citations, reference_boundary
from workbench.state import BranchState, Channel, Family
from workbench.store import ArtifactStore


class ConfigTest(unittest.TestCase):
    def test_empty_config_reports_missing_but_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[app]\nname="wb"\ndata_dir="./.data"\n', encoding="utf-8")
            cfg = config_mod.load(path)
            missing = {i["key"] for i in cfg.doctor_items() if i["kind"] == "required"}
            self.assertIn("vault.readonly.base_url", missing)
            self.assertFalse(cfg.capabilities()["ingest_materials"])

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                '[app]\nname="wb"\ndata_dir="./.data"\n[vault.readonly]\ndriver="http_rest"\n',
                encoding="utf-8",
            )
            cfg = config_mod.load(path)
            cfg.vault_readonly.values["base_url"] = "http://x"
            self.assertEqual(cfg.vault_readonly.get("base_url"), "http://x")


class StateTest(unittest.TestCase):
    def test_fanout_happens_once(self):
        fam = Family.open("f1", "t", [Channel.WECHAT, Channel.XHS], "article_first")
        fam.fan_out()
        with self.assertRaises(InvariantViolation):
            fam.fan_out()

    def test_invalid_transition_rejected(self):
        fam = Family.open("f1", "t", [Channel.WECHAT], "article_first")
        branch = fam.branches[Channel.WECHAT]
        with self.assertRaises(InvariantViolation):
            branch.move(BranchState.PUBLISHED)   # 不能从 queued 直接发布

    def test_published_is_terminal_and_aggregate_partial(self):
        fam = Family.open("f1", "t", [Channel.WECHAT, Channel.XHS], "article_first")
        wechat, xhs = fam.branches[Channel.WECHAT], fam.branches[Channel.XHS]
        wechat.move(BranchState.RUNNING)
        wechat.move(BranchState.READY_TO_PUBLISH)
        wechat.move(BranchState.PUBLISHED)     # 必须经"可发布"再到"已发布"
        xhs.move(BranchState.RUNNING)          # 一条成功一条在跑 => partial
        self.assertEqual(fam.aggregate(), "partial")
        with self.assertRaises(InvariantViolation):
            wechat.move(BranchState.RUNNING)   # 已发布不可回退


class GateTest(unittest.TestCase):
    def _card(self, **kw):
        base = dict(
            gate=Gate.DRAFT, ask="建议发布", recommendation="approve", confidence="中",
            reasons=["一"], metrics={}, risk="无", not_worried="无",
            done=[], todo=[], alternatives=[], question="发不发？",
        )
        base.update(kw)
        return Card(**base)

    def test_card_without_recommendation_is_rejected(self):
        with self.assertRaises(InvariantViolation):
            self._card(recommendation="")

    def test_ask_too_long_and_too_many_alternatives(self):
        with self.assertRaises(InvariantViolation):
            self._card(ask="很长" * 20)
        with self.assertRaises(InvariantViolation):
            self._card(alternatives=["a", "b", "c"])

    def test_custom_reply_low_confidence_never_executes(self):
        r = parse_custom_reply("随便改改", confidence=0.3)
        self.assertIs(r.intent, Intent.UNPARSED)
        self.assertFalse(r.confirmed)

    def test_custom_reply_redirect_needs_confirmation(self):
        r = parse_custom_reply("换个角度，别做盘点", confidence=0.9)
        self.assertIs(r.intent, Intent.REDIRECT)
        self.assertFalse(r.confirmed)                 # 默认未确认
        self.assertIn("对吗", r.echo)


class LedgerTest(unittest.TestCase):
    def test_always_ask_topics_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Ledger(Path(tmp) / "d.json")
            led.add("spend", "额度随便用", "不设上限")
            self.assertIsNone(led.reuse_allowed("spend"))

    def test_reuse_requires_same_conditions_and_echoes_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Ledger(Path(tmp) / "d.json")
            d = led.add("platforms", "先跑三家", "只做三家", {"track": "ai"})
            self.assertEqual(led.reuse_allowed("platforms", conditions={"track": "ai"}).id, d.id)
            self.assertIsNone(led.reuse_allowed("platforms", conditions={"track": "food"}))
            self.assertIn("先跑三家", led.echo(d))


class QCTest(unittest.TestCase):
    def test_number_drift_detected(self):
        notes = [Note("a.md", "a", "实测耗时 12 分钟", {"origin": "https://x"})]
        finding = reconcile_citations("实测耗时 20 分钟", notes)
        self.assertFalse(finding.passed)
        self.assertIn("20", finding.detail)

    def test_identifier_digits_not_treated_as_claim(self):
        notes = [Note("a.md", "a", "正文", {})]
        self.assertTrue(reconcile_citations("母版 narr-5c03a954 已定", notes).passed)

    def test_hedge_upgraded_to_assertion_is_blocked(self):
        notes = [Note("a.md", "a", "据说这款工具很稳", {})]
        finding = reconcile_citations("数据显示这款工具很稳", notes)
        self.assertFalse(finding.passed)

    def test_reference_link_in_body_is_blocked(self):
        refs = [ReferenceItem("https://b.example/x", "t", "blog", "now", 200, "h", "e")]
        self.assertFalse(reference_boundary("见 https://b.example/x 的说法", refs).passed)

    def test_negative_named_accusation_blocked(self):
        self.assertFalse(attribution_redline("某某博主就是恰饭").passed)


class PipelineTest(unittest.TestCase):
    def _pipeline(self, mode="manual", platforms=(Channel.WECHAT, Channel.XHS), notifier=None):
        tmp = tempfile.mkdtemp()
        return Pipeline(
            store=ArtifactStore(Path(tmp)), audit=Audit(Path(tmp)),
            vault_read=None, vault_write=None, retrieval=StubRetrieval(),
            gateway=StubGateway(UsageLedger()), notifier=notifier or FileNotifier(Path(tmp) / "cards"),
            publish_mode=mode, platforms=platforms,
        )

    def test_manual_mode_delivers_files_without_publish_card(self):
        result = self._pipeline("manual").run("测试话题")
        states = {b.state for b in result.family.branches.values()}
        self.assertEqual(states, {BranchState.MANUAL_UPLOAD})
        self.assertEqual(len(result.card_paths), 1)          # 只有 G1，不弹发布卡
        self.assertTrue(Path(result.card_paths[0]).exists())

    def test_auto_mode_marks_ready_to_publish(self):
        result = self._pipeline("auto").run("测试话题")
        states = {b.state for b in result.family.branches.values()}
        self.assertEqual(states, {BranchState.READY_TO_PUBLISH})

    def test_branch_failure_does_not_take_down_siblings(self):
        pipe = self._pipeline(platforms=(Channel.WECHAT, Channel.XHS, Channel.DOUYIN))
        original = pipe._branch

        def flaky(family, channel, shared):
            if channel is Channel.DOUYIN:
                raise RuntimeError("渲染挂了")
            return original(family, channel, shared)

        pipe._branch = flaky
        result = pipe.run("测试话题")
        self.assertIs(result.family.branches[Channel.DOUYIN].state, BranchState.FAILED)
        self.assertIs(result.family.branches[Channel.WECHAT].state, BranchState.MANUAL_UPLOAD)
        self.assertEqual(result.family.aggregate(), "partial")
        self.assertTrue(any("douyin" in n for n in result.notes))

    def test_unavailable_reference_is_reported_not_faked(self):
        result = self._pipeline().run("测试话题")
        self.assertTrue(any("抓不到" in n for n in result.notes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
