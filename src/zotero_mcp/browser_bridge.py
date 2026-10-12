"""Authenticated loopback broker for the browser Connector's save-button path.

The broker never fetches publisher pages. A paired extension claims each job
once and executes it in the user's browser. Disconnected jobs are not replayed:
a lost acknowledgement must not create another Zotero item.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

PROTOCOL = 1
DEFAULT_PORT = 23120
MAX_BODY = 1024 * 1024


class BridgeError(RuntimeError):
    code = "browser_bridge_error"


def read_token(path: str, *, create: bool = False) -> str:
    file = Path(path).expanduser()
    if create:
        file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(file, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
                out.write(secrets.token_urlsafe(32) + "\n")
        except FileExistsError:
            pass
    token = file.read_text().strip()
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise BridgeError("Bridge token must contain at least 32 ASCII characters without whitespace")
    return token


class Broker:
    def __init__(self):
        self.lock = threading.RLock()
        self.id = str(uuid.uuid4())
        self.jobs: dict[str, dict] = {}
        self.client = None
        self.last_seen = 0.0

    def submit(self, data):
        action = data.get("action")
        if action not in ("tabs", "targets", "open", "inspect", "focus", "save"):
            raise BridgeError("Unknown browser action")
        if action in ("open", "inspect", "focus", "save"):
            url, tab = data.get("url"), data.get("tab_id")
            if (url is None) == (tab is None):
                raise BridgeError("Provide exactly one URL or tab_id")
            if url is not None:
                if not isinstance(url, str):
                    raise BridgeError("URL must be an HTTP(S) string")
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                    raise BridgeError("URL must be HTTP(S), without embedded credentials")
            elif type(tab) is not int or tab < 0:
                raise BridgeError("tab_id must be a non-negative integer from browser tabs")
            if action in ("inspect", "focus") and url is not None:
                raise BridgeError(f"{action} requires an existing tab_id")
            if action == "open" and tab is not None:
                raise BridgeError("open requires a URL")
        for option in ("background", "close_on_success"):
            if option in data and type(data[option]) is not bool:
                raise BridgeError(f"{option} must be boolean")
        if data.get("background") and (action not in ("open", "save") or not data.get("url")):
            raise BridgeError("background requires an open/save URL")
        if data.get("close_on_success") and (action != "save" or not data.get("url") or not data.get("background")):
            raise BridgeError("close_on_success requires a background save URL")
        if action == "save":
            target = data.get("target")
            if target is not None and (not isinstance(target, str) or re.fullmatch(r"[LC][1-9][0-9]*", target) is None):
                raise BridgeError("target must be a Connector target ID from browser targets")
        with self.lock:
            # Bound memory without silently evicting an unresolved save result.
            if len(self.jobs) >= 1000:
                raise BridgeError("Broker job limit reached; inspect results before restarting it")
            job_id = str(uuid.uuid4())
            self.jobs[job_id] = {"id": job_id, "state": "queued", "request": data}
            return dict(self.jobs[job_id])

    def poll(self, client):
        if not isinstance(client, str) or not 1 <= len(client) <= 128:
            raise BridgeError("Invalid Connector client ID")
        with self.lock:
            if self.client and self.client != client:
                raise BridgeError("Another Connector is paired with this broker; use a separate port/token")
            self.client, self.last_seen = client, time.monotonic()
            # Serialize browser actions, including while a save awaits human input.
            if any(j["state"] == "running" for j in self.jobs.values()):
                return None
            for job in self.jobs.values():
                if job["state"] == "queued":
                    job["state"] = "running"
                    return dict(job)
            return None

    def finish(self, job_id, client, result):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or client != self.client or job["state"] not in ("running", "done"):
                raise BridgeError("Unknown or unclaimed job")
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise BridgeError("Malformed Connector result")
            if job["state"] == "done" and job["result"] != result:
                raise BridgeError("Conflicting result for completed job")
            job.update(state="done", result=result)
            return dict(job)

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise BridgeError("Unknown job; results only persist for the broker's lifetime")
            return dict(self.jobs[job_id])

    def status(self):
        with self.lock:
            return {"protocol": PROTOCOL, "brokerID": self.id, "connected": bool(self.client and time.monotonic() - self.last_seen < 15),
                    "jobs": len(self.jobs)}


def make_server(token: str, port: int = DEFAULT_PORT):
    broker = Broker()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not retain authentication headers or browser URLs.

        def send_json(self, status, data):
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def dispatch(self):
            # No website CORS access. Extension fetches use the explicitly granted
            # localhost host permission and an unguessable pairing token.
            origin = self.headers.get("Origin", "")
            if origin and not origin.startswith(("chrome-extension://", "moz-extension://")):
                return self.send_json(403, {"error": "Website origins are not allowed"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.send_json(401, {"error": "Invalid bridge token"})
            try:
                data = {}
                if self.command == "POST":
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_BODY:
                        raise BridgeError("Invalid request size")
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise BridgeError("Expected JSON object")
                if self.command == "GET" and self.path == "/status":
                    result = broker.status()
                elif self.command == "POST" and self.path == "/jobs":
                    result = broker.submit(data)
                elif self.command == "POST" and self.path == "/poll":
                    result = {"protocol": PROTOCOL, "brokerID": broker.id, "job": broker.poll(data.get("client"))}
                elif self.command == "GET" and self.path.startswith("/jobs/"):
                    result = broker.get(self.path[6:])
                elif self.command == "POST" and self.path.startswith("/jobs/"):
                    result = broker.finish(self.path[6:], data.get("client"), data.get("result"))
                else:
                    return self.send_json(404, {"error": "Unknown bridge endpoint"})
                self.send_json(200, result)
            except (BridgeError, ValueError, TypeError) as exc:
                self.send_json(400, {"error": str(exc)})

        do_GET = dispatch
        do_POST = dispatch

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.broker = broker
    return server


class BridgeClient:
    def __init__(self, token, port=DEFAULT_PORT):
        self.token = token
        self.base = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, data=None):
        payload = json.dumps(data).encode() if data is not None else None
        req = Request(self.base + path, data=payload,
                      headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=10) as response:
                return json.load(response)
        except Exception as exc:
            # urllib errors do not include request headers; never include token.
            raise BridgeError(f"Bridge request failed: {exc}") from exc

    def run(self, request, timeout=120):
        if not self.request("/status")["connected"]:
            raise BridgeError("No paired Connector is polling. Enable Browser CLI in Connector preferences.")
        job = self.request("/jobs", request)
        return self.wait(job["id"], timeout)

    def wait(self, job_id, timeout):
        deadline = time.monotonic() + timeout
        while True:
            job = self.request("/jobs/" + job_id)
            if job["state"] == "done":
                return job
            if time.monotonic() >= deadline:
                raise BridgeError(f"Browser job {job_id} is {job['state']}; its save outcome is not yet known. "
                                  f"Use browser result {job_id}; do not submit another save.")
            time.sleep(0.25)
