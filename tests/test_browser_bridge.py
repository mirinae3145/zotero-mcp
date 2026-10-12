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

    def test_invalid_actions_and_tab_ids(self):
        for request in [{"action": "eval"}, {"action": "save", "tab_id": True},
                        {"action": "save", "tab_id": -1},
                        {"action": "save", "tab_id": 1, "target": "6181240"}]:
            with self.assertRaises(BridgeError):
                self.server.broker.submit(request)


if __name__ == "__main__":
    unittest.main()
