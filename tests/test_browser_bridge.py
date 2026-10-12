"""Contract tests for the real HTTP broker, without a user's library."""
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from zotero_mcp.browser_bridge import BridgeClient, BridgeError, make_server, read_token


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server("t" * 40, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = BridgeClient("t" * 40, self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_authentication_and_website_origin(self):
        for headers, status in [({}, 401),
                ({"Authorization": "Bearer " + "t" * 40, "Origin": "https://evil.example"}, 403)]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.client.base + "/status", headers=headers))
            self.assertEqual(error.exception.code, status)

    def test_group_save_roundtrip_preserves_receipt(self):
        self.client.request("/poll", {"client": "extension-one"})
        job = self.client.request("/jobs", {"action": "save", "tab_id": 8, "target": "L4"})
        claimed = self.client.request("/poll", {"client": "extension-one"})["job"]
        self.assertEqual(claimed["request"]["target"], "L4")
        self.assertIsNone(self.client.request("/poll", {"client": "extension-one"})["job"])
        result = {"ok": True, "sessionID": "session-one", "target": "L4", "items": []}
        self.client.request("/jobs/" + job["id"], {"client": "extension-one", "result": result})
        self.assertEqual(self.client.wait(job["id"], 1)["result"], result)
        # Lost acknowledgement is safe to resend, but never replays the save.
        self.client.request("/jobs/" + job["id"], {"client": "extension-one", "result": result})
        self.assertIsNone(self.client.request("/poll", {"client": "extension-one"})["job"])

    def test_timeout_does_not_requeue_running_save(self):
        broker = self.server.broker
        broker.poll("one")
        job = broker.submit({"action": "save", "tab_id": 3})
        broker.poll("one")
        with self.assertRaisesRegex(BridgeError, job["id"]):
            self.client.wait(job["id"], 0)
        self.assertEqual(broker.get(job["id"])["state"], "running")
        self.assertIsNone(broker.poll("one"))

    def test_second_extension_cannot_claim_jobs(self):
        broker = self.server.broker
        broker.poll("one")
        with self.assertRaises(BridgeError):
            broker.poll("two")

    def test_failed_pdf_result_is_preserved(self):
        broker = self.server.broker
        broker.poll("one")
        job = broker.submit({"action": "save", "tab_id": 3})
        broker.poll("one")
        result = {"ok": False, "sessionID": "known", "error": "PDF failed", "items": [{"title": "Saved metadata"}]}
        broker.finish(job["id"], "one", result)
        self.assertEqual(self.client.wait(job["id"], 1)["result"], result)

    def test_token_file_creation_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "bridge.token")
            token = read_token(path, create=True)
            self.assertGreaterEqual(len(token), 32)
            self.assertEqual(read_token(path, create=True), token)

    def test_urls_must_be_http_and_unambiguous(self):
        for request in [{"action":"save","url":"file:///tmp/paper.pdf"},
                        {"action":"save","url":"https://user:secret@example.com"},
                        {"action":"save","url":"https://example.com","tab_id":3},
                        {"action":"inspect","url":"https://example.com"}]:
            with self.assertRaises(BridgeError):
                self.server.broker.submit(request)
        job=self.server.broker.submit({"action":"save","url":"https://doi.org/10.1/example"})
        self.assertEqual(job["state"],"queued")

    def test_invalid_actions_and_tab_ids(self):
        for request in [{"action": "eval"}, {"action": "save", "url": "https://example.org", "destination": "group123"}, {"action": "save", "tab_id": True},
                        {"action": "save", "tab_id": -1},
                        {"action": "save", "tab_id": 1, "target": "6181240"}]:
            with self.assertRaises(BridgeError):
                self.server.broker.submit(request)

    def test_background_lifecycle_contract(self):
        broker = self.server.broker
        for request in [
            {"action": "save", "url": "https://example.org", "background": "yes"},
            {"action": "save", "tab_id": 1, "close_on_success": True},
            {"action": "save", "url": "https://example.org", "close_on_success": True},
            {"action": "focus", "url": "https://example.org"},
            {"action": "open", "url": "https://example.org", "close_on_success": True},
        ]:
            with self.assertRaises(BridgeError):
                broker.submit(request)
        request = {"action": "save", "url": "https://example.org", "background": True, "close_on_success": True}
        job = broker.submit(request)
        self.assertEqual(broker.poll("test")["request"], request)
        broker.finish(job["id"], "test", {"ok": True, "tab": {"closed": True}})
        self.assertTrue(broker.get(job["id"])["result"]["tab"]["closed"])
        self.assertEqual(broker.submit({"action": "focus", "tab_id": 1})["request"]["tab_id"], 1)


class BrowserBatchTests(unittest.TestCase):
    def test_batch_defers_attention_without_focus_and_paces_saves(self):
        from unittest.mock import Mock, patch
        from zotero_mcp.browser_bridge import save_batch
        client = Mock()
        client.request.side_effect = lambda path, data=None: {"connected": True} if path == "/status" else {"id": data["url"]}
        results = [{"ok": False, "state": "needs_user_action", "tab": {"id": 1}},
                   {"ok": True, "tab": {"id": 2, "closed": True}},
                   {"ok": False, "desktopSaved": True, "items": [], "tab": {"id": 3}}]
        client.wait.side_effect = [{"result": r} for r in results]
        with patch("zotero_mcp.browser_bridge.time.sleep") as sleep:
            report = save_batch(client, ["https://example.org/a", "https://example.org/b", "https://example.org/c"], "L4")
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([len(report[k]) for k in ["completed", "deferred", "failed", "unattempted"]], [1, 1, 1, 0])
        self.assertFalse(report["stopped"])
        for call in client.request.call_args_list:
            if call.args[0] == "/jobs":
                self.assertTrue(call.args[1]["background"])
                self.assertTrue(call.args[1]["close_on_success"])
                self.assertTrue(call.args[1]["defer_user_action"])
                self.assertEqual(call.args[1]["action"], "save")

    def test_batch_timeout_retains_job_and_stops_without_resubmission(self):
        from unittest.mock import Mock
        from zotero_mcp.browser_bridge import save_batch
        client = Mock()
        client.request.side_effect = [{"connected": True}, {"id": "pending"}]
        client.wait.side_effect = BridgeError("pending outcome unknown")
        report = save_batch(client, ["https://example.org/a", "https://example.org/b"], "L4")
        self.assertTrue(report["stopped"])
        self.assertEqual(report["failed"][0]["job_id"], "pending")
        self.assertEqual(report["unattempted"], ["https://example.org/b"])
        self.assertEqual(client.request.call_count, 2)

    def test_batch_validates_whole_plan_before_writing(self):
        from unittest.mock import Mock
        from zotero_mcp.browser_bridge import save_batch
        for urls, interval in [(["https://example.org/a", "file:///bad"], 2),
                               (["https://example.org/a"] * 2, 2),
                               (["https://example.org/a"], float("nan")),
                               (["https://example.org/a"], 0)]:
            client = Mock()
            with self.assertRaises(BridgeError):
                save_batch(client, urls, "L4", interval)
            client.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def test_destination_batch_drops_initial_guard_after_first_paper():
    from unittest.mock import Mock, patch
    from zotero_mcp.browser_bridge import save_batch
    client = Mock()
    client.request.side_effect = lambda path, data=None: {"connected": True} if path == "/status" else {"id": "job"}
    client.wait.return_value = {"result": {"ok": True, "destinationApplied": True}}
    with patch("zotero_mcp.browser_bridge.time.sleep"):
        result = save_batch(client, ["https://example.org/a", "https://example.org/b"], "L4", destination="L5")
    requests = [c.args[1] for c in client.request.call_args_list if c.args[0] == "/jobs"]
    assert result["ok"]
    assert [r["target"] for r in requests] == ["L4", None]
    assert all(r["destination"] == "L5" for r in requests)


def test_unconfirmed_destination_receipt_is_not_success():
    client = BridgeClient("test")
    client.request = lambda *_: {"state": "done", "request": {"destination": "L5"},
        "result": {"ok": True, "target": "L4", "sessionID": "partial"}}
    result = client.wait("job", 1)["result"]
    assert result["ok"] is False
    assert result["destinationApplied"] is False
    assert result["sessionID"] == "partial"


def test_cli_refuses_old_connector_before_destination_save():
    from unittest.mock import patch
    from zotero_mcp.cli_standalone import build_parser, cmd_browser
    args = build_parser().parse_args(["browser", "save", "--url", "https://example.org/paper", "--destination", "L5"])
    with patch("zotero_mcp.browser_bridge.read_token", return_value="test"), patch("zotero_mcp.browser_bridge.BridgeClient") as client:
        client.return_value.run.return_value = {"result": {"ok": True, "targets": []}}
        with __import__("pytest").raises(BridgeError, match="update and reload"):
            cmd_browser(args)
        client.return_value.run.assert_called_once_with({"action": "targets"}, args.timeout)
