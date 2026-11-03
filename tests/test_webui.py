"""WebUI 契约测试：认证、白名单改写、闸门确认回执、局域网启动保护。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.errors import WorkbenchError
from workbench.webui.server import Ctx, Handler, _patch_toml, serve

TOKEN = "test-token-123"
CONFIG = """
[app]
name = "wb"
data_dir = "{data}"

[vault.readonly]
driver = "http_rest"
base_url = ""
token = ""

[retrieval]
provider = "stub"

[gateway]
provider = "stub"

[notify]
driver = "file"
out_dir = "{cards}"

[publish]
mode = "manual"
platforms = ["wechat_mp", "xiaohongshu"]
"""


class WebUITest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg_path = self.tmp / "config.toml"
        self.cfg_path.write_text(
            CONFIG.format(data=self.tmp / ".data", cards=self.tmp / "cards"), encoding="utf-8"
        )
        Handler.ctx = Ctx(config_path=self.cfg_path, token=TOKEN)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, *, method="GET", body=None, token=TOKEN):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if token:
            req.add_header("X-WB-Token", token)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_page_served_without_token(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as resp:
            html = resp.read().decode()
        self.assertIn("内容生产工作台", html)

    def test_api_requires_token(self):
        code, body = self.call("/api/status", token="")
        self.assertEqual(code, 401)
        self.assertIn("口令", body["error"])

    def test_status_reports_missing_capabilities(self):
        code, body = self.call("/api/status")
        self.assertEqual(code, 200)
        self.assertFalse(body["capabilities"]["ingest_materials"])
        self.assertTrue(any(i["key"] == "vault.readonly.base_url" for i in body["items"]))

    def test_secret_values_are_masked(self):
        _patch_toml(self.cfg_path, "vault.readonly.token", "super-secret-value")
        code, body = self.call("/api/config")
        row = next(r for r in body["rows"] if r["key"] == "vault.readonly.token")
        self.assertEqual(code, 200)
        self.assertTrue(row["filled"])
        self.assertNotIn("super-secret-value", json.dumps(body, ensure_ascii=False))

    def test_config_write_is_whitelisted(self):
        code, body = self.call("/api/config", method="POST",
                               body={"key": "vault.readonly.base_url", "value": "http://10.0.0.5:27124"})
        self.assertEqual(code, 200)
        self.assertIn("http://10.0.0.5:27124", self.cfg_path.read_text(encoding="utf-8"))
        code, body = self.call("/api/config", method="POST", body={"key": "app.name", "value": "hack"})
        self.assertEqual(code, 400)

    def test_run_then_decide_card_lands_in_ledger(self):
        code, body = self.call("/api/run", method="POST", body={"topic": "界面冒烟", "with_vault": False})
        self.assertEqual(code, 200)
        for _ in range(100):                                  # 等后台线程
            _code, cards = self.call("/api/cards")
            if cards["cards"]:
                break
            threading.Event().wait(0.05)
        card = cards["cards"][0]
        self.assertEqual(card["gate"], "G1")

        code, applied = self.call("/api/cards/decide", method="POST",
                                  body={"gate_id": card["gate_id"], "action": "approve"})
        self.assertEqual(code, 200)
        self.assertTrue(applied["applied"])
        _code, ledger = self.call("/api/ledger")
        self.assertTrue(any(card["gate_id"] in d["topic"] for d in ledger["decisions"]))

    def test_custom_reply_needs_confirmation_before_execution(self):
        self.call("/api/run", method="POST", body={"topic": "回执测试", "with_vault": False})
        for _ in range(100):
            _code, cards = self.call("/api/cards")
            if cards["cards"]:
                break
            threading.Event().wait(0.05)
        gate_id = cards["cards"][0]["gate_id"]

        code, receipt = self.call("/api/cards/decide", method="POST",
                                  body={"gate_id": gate_id, "action": "custom",
                                        "text": "换个角度，别做盘点", "confidence": 0.9})
        self.assertEqual(code, 200)
        self.assertFalse(receipt["applied"])                  # 未确认不执行
        self.assertEqual(receipt["intent"], "redirect")
        self.assertIn("对吗", receipt["echo"])

        _code, before = self.call("/api/ledger")
        code, done = self.call("/api/cards/confirm", method="POST",
                               body={"receipt_id": receipt["receipt_id"], "confirmed": False})
        self.assertFalse(done["applied"])                     # 作废也不写台账
        _code, after = self.call("/api/ledger")
        self.assertEqual(len(before["decisions"]), len(after["decisions"]))

    def test_low_confidence_reply_is_not_confirmable(self):
        self.call("/api/run", method="POST", body={"topic": "低置信", "with_vault": False})
        for _ in range(100):
            _code, cards = self.call("/api/cards")
            if cards["cards"]:
                break
            threading.Event().wait(0.05)
        gate_id = cards["cards"][0]["gate_id"]
        _code, receipt = self.call("/api/cards/decide", method="POST",
                                   body={"gate_id": gate_id, "action": "custom",
                                         "text": "嗯", "confidence": 0.2})
        self.assertEqual(receipt["intent"], "unparsed")
        self.assertFalse(receipt["needs_confirm"])


class LanSafetyTest(unittest.TestCase):
    def test_refuses_lan_bind_without_token(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "config.toml"
        path.write_text(CONFIG.format(data=tmp / ".data", cards=tmp / "cards"), encoding="utf-8")
        with self.assertRaises(WorkbenchError) as ctx:
            serve(path, host="0.0.0.0", port=0, token="")
        self.assertIn("口令", str(ctx.exception))


class TomlPatchTest(unittest.TestCase):
    def test_patch_preserves_comments_and_other_sections(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "c.toml"
        path.write_text('# 头部注释\n[app]\nname = "wb"  # 行内注释\n\n[vault.readonly]\ntoken = ""\n',
                        encoding="utf-8")
        _patch_toml(path, "vault.readonly.token", "abc")
        text = path.read_text(encoding="utf-8")
        self.assertIn("# 头部注释", text)
        self.assertIn("# 行内注释", text)
        self.assertIn('token = "abc"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
