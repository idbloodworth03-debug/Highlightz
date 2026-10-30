"""The 2026-10-29 freeze, and the three guards against another.

What happened (production's journal and nginx logs, read 2026-10-30): Kick's
chat webhooks, one POST per message of 13 channels and over 99% of all
requests, rose through the evening to 44k per 10 minutes. The server fell
behind, the requests in flight piled up past the process's open-file limit
(1024), and accept() failed with "[Errno 24] Too many open files" ~13,000
times in 15 minutes. The site answered nobody until the owner rebooted the
box. The process never exited, so systemd's Restart=always never fired.

What these pin:
  1. the server raises its own open-file limit at startup, never lowers it,
     and the unit asks for the same;
  2. a Kick delivery is answered in front of the site's middleware, with the
     route's whole contract (signature, 400/401/413, 200 for anything signed),
     and everything else still goes through the site as before;
  3. deploy/healthcheck.sh restarts the service after three missed /health
     checks (not after one, which a deploy's restart can cost), resets on an
     answer, leaves a marker with what it saw, and the next start reports it
     as an error, which reaches the owner's alert email.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from src import main as server
from src.chat.platform import kick_events as ke
from src.dashboard import api

ROOT = Path(__file__).resolve().parents[1]
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


# ── 1. the open-file limit ───────────────────────────────────────────────────

class _FakeResource:
    RLIMIT_NOFILE = 7
    RLIM_INFINITY = -1

    def __init__(self, soft, hard, fail=False):
        self.limits, self.fail, self.calls = (soft, hard), fail, []

    def getrlimit(self, which):
        if self.fail:
            raise OSError("no")
        return self.limits

    def setrlimit(self, which, limits):
        self.calls.append(limits)
        self.limits = limits


@pytest.mark.parametrize("soft,hard,want", [
    (1024, 524288, (65536, 524288)),     # production's numbers
    (1024, 4096, (4096, 4096)),          # a hard limit below the target is as far as it goes
    (100000, 524288, (100000, 524288)),  # never lowered
    (1024, -1, (65536, -1)),             # an unlimited hard limit
])
def test_the_server_raises_its_own_open_file_limit(monkeypatch, soft, hard, want):
    fake = _FakeResource(soft, hard)
    monkeypatch.setitem(__import__("sys").modules, "resource", fake)
    assert server.raise_open_file_limit() == want
    assert fake.calls == ([] if want == (soft, hard) else [want])


def test_a_limit_it_cannot_read_never_stops_the_server(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "resource", _FakeResource(0, 0, fail=True))
    assert server.raise_open_file_limit() == (-1, -1)


def test_startup_raises_the_limit_and_the_unit_asks_for_it():
    import inspect
    src = inspect.getsource(server.main)
    assert "raise_open_file_limit()" in src and "take_health_restart_marker()" in src
    unit = (ROOT / "deploy" / "highlightz.service").read_text()
    assert "\nLimitNOFILE=65536" in unit


def test_the_real_limit_here_is_raised():
    import resource
    soft, hard = server.raise_open_file_limit()
    assert soft == min(server.NOFILE_TARGET, hard) or soft >= server.NOFILE_TARGET
    assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] == soft


# ── 2. Kick's deliveries skip the site's middleware ──────────────────────────

def _chat(mid="chat-1") -> bytes:
    return json.dumps({"message_id": mid, "broadcaster": {"user_id": 1001},
                       "sender": {"username": "viewer1"}, "content": "what a play"}).encode()


def _signed(body: bytes, delivery="01JDELIVERY0001") -> dict:
    ts = "2026-10-29T03:10:00Z"
    sig = KEY.sign(f"{delivery}.{ts}.".encode() + body, padding.PKCS1v15(), hashes.SHA256())
    return {"Kick-Event-Message-Id": delivery, "Kick-Event-Message-Timestamp": ts,
            "Kick-Event-Signature": base64.b64encode(sig).decode(),
            "Kick-Event-Type": "chat.message.sent", "content-type": "application/json"}


@pytest.fixture
def kick(monkeypatch):
    monkeypatch.setattr(ke, "_key", KEY.public_key())
    monkeypatch.setattr(ke, "_seen", {})
    # A forged signature asks for a fresh key once; it is rate-limited, so a
    # recent attempt keeps the test off the network.
    monkeypatch.setattr(ke, "_key_tried", __import__("time").time())
    q = ke.listen("1001")
    yield TestClient(api.app, base_url="https://testserver"), q
    ke.unlisten("1001", q)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_a_kick_delivery_is_answered_before_the_site_middleware(kick):
    c, q = kick
    body = _chat()
    r = c.post("/kick/webhook", content=body, headers=_signed(body))
    assert r.status_code == 200 and _drain(q) == [("viewer1", "what a play")]
    # Proof it never went through them: no session cookie, no security headers.
    assert "set-cookie" not in r.headers
    assert "content-security-policy" not in r.headers and "x-frame-options" not in r.headers
    # The rest of the site still goes through them.
    page = c.get("/compare")
    assert "content-security-policy" in page.headers


def test_the_fast_path_keeps_the_routes_contract(kick):
    c, q = kick
    body = _chat()
    assert c.post("/kick/webhook", content=body).status_code == 400            # unsigned
    tampered = body.replace(b"what a play", b"forged")
    assert c.post("/kick/webhook", content=tampered, headers=_signed(body)).status_code == 401
    big = b"x" * (ke.MAX_BODY + 1)
    assert c.post("/kick/webhook", content=big, headers=_signed(big)).status_code == 413
    assert _drain(q) == []
    # Anything that is not a POST to exactly /kick/webhook is the site's, as before.
    assert c.get("/kick/webhook", follow_redirects=False).status_code != 200
    assert c.post("/kick/webhook/", content=b"{}", follow_redirects=False).status_code != 200


def test_a_streamed_body_over_the_limit_is_refused_without_a_length(kick):
    import asyncio
    sent = []

    async def go():
        chunks = [b"x" * 40_000, b"x" * 40_000]
        async def receive():
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        async def send(m):
            sent.append(m)
        scope = {"type": "http", "method": "POST", "path": "/kick/webhook", "headers": []}
        await api.KickWebhookFastPath(app=None)(scope, receive, send)

    asyncio.run(go())
    assert sent[0]["status"] == 413


def test_an_unexpected_error_is_logged_and_answered_500(kick, monkeypatch):
    c, _ = kick

    async def boom(headers, body):
        raise RuntimeError("disk full")
    monkeypatch.setattr(ke, "handle_delivery", boom)
    body = _chat()
    assert c.post("/kick/webhook", content=body, headers=_signed(body)).status_code == 500


def test_the_fast_path_is_the_outermost_layer():
    assert api.app.user_middleware[0].cls is api.KickWebhookFastPath


# ── 3. the health check restarts a server that stopped answering ─────────────

needs_shell = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("curl")),
                                 reason="needs bash and curl")


def _run_check(tmp_path, url):
    env = {**os.environ, "HEALTH_URL": url, "HEALTH_SKIP_ACTIVE_CHECK": "1",
           "HEALTH_STATE": str(tmp_path / "fails"), "HEALTH_MARKER": str(tmp_path / "marker.json"),
           "HEALTH_RESTART_CMD": f"touch {tmp_path / 'restarted'}", "HEALTH_SERVICE": "no-such-unit"}
    subprocess.run(["bash", str(ROOT / "deploy" / "healthcheck.sh")], env=env, check=True,
                   capture_output=True, timeout=60)
    return (tmp_path / "fails").read_text().strip(), (tmp_path / "restarted").exists()


@needs_shell
def test_three_missed_checks_restart_the_service_and_one_does_not(tmp_path):
    dead = "http://127.0.0.1:9/health"            # nothing listens on the discard port
    assert _run_check(tmp_path, dead) == ("1", False)
    assert _run_check(tmp_path, dead) == ("2", False)
    assert _run_check(tmp_path, dead) == ("0", True)
    marker = json.loads((tmp_path / "marker.json").read_text())
    assert marker["fails"] == 3 and marker["at"] > 0 and "open_files" in marker


@needs_shell
def test_an_answer_resets_the_count(tmp_path):
    class Ok(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        dead = "http://127.0.0.1:9/health"
        _run_check(tmp_path, dead)
        _run_check(tmp_path, dead)
        assert _run_check(tmp_path, f"http://127.0.0.1:{srv.server_port}/health") == ("0", False)
        assert _run_check(tmp_path, dead) == ("1", False), "the count started over"
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_health_check_asks_the_apps_open_health_page():
    script = (ROOT / "deploy" / "healthcheck.sh").read_text()
    assert "http://127.0.0.1:8000/health" in script
    assert "/health" in api._OPEN_PATHS
    c = TestClient(api.app, base_url="https://testserver")
    assert c.get("/health", follow_redirects=False).json() == {"status": "ok"}
    cron = (ROOT / "deploy" / "highlightz-health.cron").read_text()
    assert "* * * * * root /bin/bash /opt/highlightz/deploy/healthcheck.sh" in cron


def test_the_next_start_reports_the_restart_once(tmp_path):
    (tmp_path / server.HEALTH_MARKER).write_text('{"at": 1761794700, "fails": 3, "open_files": 1024}\n')
    assert server.take_health_restart_marker(str(tmp_path)) == {"at": 1761794700, "fails": 3,
                                                                "open_files": 1024}
    assert not (tmp_path / server.HEALTH_MARKER).exists()
    assert server.take_health_restart_marker(str(tmp_path)) is None
    (tmp_path / server.HEALTH_MARKER).write_text("not json")
    assert server.take_health_restart_marker(str(tmp_path)) == {}, "a garbled marker still reports"
