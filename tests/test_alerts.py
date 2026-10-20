"""Email alerts when something breaks (owner, 2026-10-20: "alerts to email").

Two failures ran for days before a customer noticed — a week of Stripe
webhook 500s, and Kick refusing chat subscriptions 733 times in a day — and
the log lines had no level to tell an error from routine output.

What these pin:
  1. every log line now carries its level, log.exception keeps its traceback,
     and the alert tap and loop are wired into main;
  2. what counts: any error, any `request_failed`, or one warning repeated
     `alert_warning_burst` times in an hour — and never the alerter's own
     events or anything in ALERT_IGNORE; stdlib errors (uvicorn, asyncio)
     count too, and still reach stderr;
  3. how often: one email per check naming everything due, each event at most
     once per cooldown, a daily cap, a back-off after a failed send;
  4. what it says, without secrets, and how it sends: Resend over HTTPS, or
     SMTP; unconfigured it sends nothing and says so once.
"""

import asyncio
import inspect
import io
import json
import logging

import pytest
import structlog

from config.settings import settings
from src import alerts


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(alerts, "_seen", {})
    monkeypatch.setattr(alerts, "_sent", [])
    monkeypatch.setattr(alerts, "_next_try", 0.0)
    monkeypatch.setattr(alerts, "_said_unconfigured", False)
    monkeypatch.setattr(settings, "alert_email_to", "owner@example.com")
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "alert_warning_burst", 20)
    monkeypatch.setattr(settings, "alert_cooldown_minutes", 360)
    monkeypatch.setattr(settings, "alert_max_per_day", 12)
    monkeypatch.setattr(settings, "alert_ignore", "")


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    async def _send(subject, text):
        sent.append((subject, text))
        return True, "sent"
    monkeypatch.setattr(alerts, "send", _send)
    return sent


def _check(now):
    return asyncio.run(alerts.check_and_send(now))


# ── 1. wired in ──────────────────────────────────────────────────────────────

def test_main_logs_levels_and_tracebacks_and_feeds_the_alerts():
    from src import main
    src = inspect.getsource(main.main)
    chain = src[src.index("structlog.configure("):src.index("JSONRenderer()")]
    for piece in ("add_log_level", "format_exc_info", "alerts.processor"):
        assert piece in chain, f"{piece} is not in the log processor chain"
    assert "alerts.install_stdlib_taps()" in src and "alerts.run()" in src
    dash = inspect.getsource(main.run_dashboard)
    assert dash.index("uvicorn.Config(") < dash.index("install_uvicorn_tap()"), \
        "building uvicorn.Config re-applies its logging config and would drop the tap"


def test_a_logged_error_line_says_error_and_is_noted(capsys):
    out = io.StringIO()
    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.format_exc_info,
                                    alerts.processor, structlog.processors.JSONRenderer()],
                        logger_factory=structlog.PrintLoggerFactory(out))
    try:
        log = structlog.get_logger("t")
        try:
            raise RuntimeError("the webhook fell over")
        except RuntimeError:
            log.exception("post_crashed", item="i1")
        line = json.loads(out.getvalue().strip().splitlines()[-1])
        assert line["level"] == "error" and "RuntimeError: the webhook fell over" in line["exception"]
        assert alerts._seen["post_crashed"].level == "error"
        assert alerts._seen["post_crashed"].example["exception"] == "RuntimeError: the webhook fell over"
    finally:
        structlog.reset_defaults()


# ── 2. what counts ───────────────────────────────────────────────────────────

def test_one_error_is_enough(outbox):
    alerts.note("worker_crashed", "error", {"channel": "xqc"}, now=1000)
    assert _check(1001) and "worker_crashed" in outbox[0][0]


def test_a_request_failure_is_enough_though_it_is_a_warning(outbox):
    alerts.note("request_failed", "warning", {"path": "/billing/webhook"}, now=1000)
    assert _check(1001)


def test_a_warning_alerts_only_when_it_keeps_happening(outbox):
    for i in range(19):
        alerts.note("kick_events_subscribe_failed", "warning", {}, now=1000 + i)
    assert not _check(1100), "19 warnings in an hour is under the burst line"
    alerts.note("kick_events_subscribe_failed", "warning", {}, now=1120)
    assert _check(1121)
    assert "20 in the last hour" in outbox[0][1]


def test_warnings_spread_over_hours_never_alert(outbox):
    for i in range(40):
        alerts.note("viewer_poll_failed", "warning", {}, now=i * 400)    # one per ~7 min
    assert not _check(40 * 400)


def test_ignored_events_and_the_alerters_own_never_count(outbox, monkeypatch):
    monkeypatch.setattr(settings, "alert_ignore", "noisy_thing, other")
    alerts.note("noisy_thing", "error", {}, now=1000)
    alerts.note("alert_send_failed", "error", {}, now=1000)
    assert alerts._seen == {} and not _check(1001)


def test_stdlib_errors_count_and_still_reach_stderr(capsys):
    tap = alerts._Tap(echo=True)
    lg = logging.getLogger("asyncio-test")
    lg.propagate = False
    lg.addHandler(tap)
    try:
        try:
            raise ValueError("task blew up")
        except ValueError:
            lg.error("Task exception was never retrieved", exc_info=True)
        lg.warning("only a warning")
    finally:
        lg.removeHandler(tap)
    s = alerts._seen["stdlib:asyncio-test"]
    assert s.level == "error" and s.example["exception"] == "ValueError: task blew up"
    err = capsys.readouterr().err
    assert "Task exception was never retrieved" in err and "only a warning" in err
    assert len(alerts._seen) == 1, "a stdlib warning is echoed, not alerted on"


def test_the_root_tap_echoes_only_when_nothing_else_prints():
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        for h in list(root.handlers):
            root.removeHandler(h)
        alerts.install_stdlib_taps()
        taps = [h for h in root.handlers if isinstance(h, alerts._Tap)]
        assert len(taps) == 1 and taps[0].echo
        alerts.install_stdlib_taps()
        assert len([h for h in root.handlers if isinstance(h, alerts._Tap)]) == 1
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in before:
            root.addHandler(h)


def test_noting_never_raises():
    alerts.note(None, "error", {"x": object()})
    alerts.processor(None, "error", {"event": object(), "level": 5})
    alerts._Tap(echo=False).emit(None)


# ── 3. how often ─────────────────────────────────────────────────────────────

def test_one_email_names_everything_due(outbox):
    alerts.note("worker_crashed", "error", {}, now=1000)
    alerts.note("upgrade_failed", "error", {}, now=1000)
    assert _check(1001) and len(outbox) == 1
    assert "worker_crashed" in outbox[0][1] and "upgrade_failed" in outbox[0][1]


def test_the_same_problem_is_named_once_per_cooldown(outbox):
    alerts.note("worker_crashed", "error", {}, now=1000)
    _check(1001)
    alerts.note("worker_crashed", "error", {}, now=1500)
    assert not _check(1501), "re-alerted inside the cooldown"
    assert _check(1000 + 360 * 60 + 2), "not alerted again after the cooldown"
    assert "1 time " in outbox[1][1]


def test_never_more_than_the_daily_cap(outbox, monkeypatch):
    monkeypatch.setattr(settings, "alert_max_per_day", 2)
    for i in range(4):
        alerts.note(f"err_{i}", "error", {}, now=1000 + i * 100)
        _check(1001 + i * 100)
    assert len(outbox) == 2


def test_a_failed_send_backs_off(monkeypatch):
    calls = []

    async def _fail(subject, text):
        calls.append(subject)
        return False, "Resend answered HTTP 500"
    monkeypatch.setattr(alerts, "send", _fail)
    alerts.note("worker_crashed", "error", {}, now=1000)
    assert not _check(1001) and not _check(1061)
    assert len(calls) == 1
    _check(1001 + alerts._RETRY_S + 1)
    assert len(calls) == 2


def test_unconfigured_sends_nothing_and_says_so_once(monkeypatch, outbox):
    monkeypatch.setattr(settings, "resend_api_key", "")
    alerts.note("worker_crashed", "error", {}, now=1000)
    assert not _check(1001) and not _check(1061)
    assert outbox == [] and alerts._said_unconfigured


def test_the_loop_survives_a_bad_pass(monkeypatch):
    passes = []

    async def _flaky(now=None):
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("bad pass")
        if len(passes) == 3:
            raise asyncio.CancelledError()
        return False
    monkeypatch.setattr(alerts, "check_and_send", _flaky)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(alerts.run(interval_s=0))
    assert len(passes) == 3


# ── 4. what it says and how it sends ─────────────────────────────────────────

def test_the_email_says_what_where_and_how_to_look_without_secrets():
    alerts.note("stripe_webhook_failed", "error",
                {"path": "/billing/webhook", "status_code": 500, "access_token": "tok_live_1",
                 "customer_email": "a@b.c", "key": "k", "exception": "Traceback...\n  File x\nAttributeError: get"},
                now=1000)
    subject, text = alerts.compose(alerts.due(1001), 1001)
    assert subject == "Highlightz: stripe_webhook_failed"
    assert "path: /billing/webhook" in text and "status_code: 500" in text
    assert "exception: AttributeError: get" in text
    assert "tok_live_1" not in text and "a@b.c" not in text and "key: k" not in text
    assert "journalctl -u highlightz" in text and "ALERT_IGNORE" in text


def test_resend_is_called_over_https(monkeypatch):
    import httpx
    seen = {}

    class _Resp:
        status_code, text = 200, '{"id":"x"}'

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            seen.update(url=url, headers=headers, json=json)
            return _Resp()
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    ok, why = asyncio.run(alerts.send("Highlightz: x", "body"))
    assert ok and seen["url"] == "https://api.resend.com/emails"
    assert seen["headers"]["Authorization"] == "Bearer re_test"
    assert seen["json"]["to"] == ["owner@example.com"] and seen["json"]["subject"] == "Highlightz: x"
    _Resp.status_code, _Resp.text = 403, "domain not verified"
    ok, why = asyncio.run(alerts.send("s", "b"))
    assert not ok and "403" in why and "domain not verified" in why


def _verifies(context) -> bool:
    import ssl
    return context is not None and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_smtp_is_the_fallback_logs_in_and_checks_the_certificate(monkeypatch):
    """The connection carries the mail account's password, and smtplib's own
    default TLS context does not verify the server."""
    import smtplib
    log = []

    class _SMTP:
        def __init__(self, host, port, timeout=None): log.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): log.append(("starttls", _verifies(context)))
        def login(self, u, p): log.append(("login", u))
        def send_message(self, msg): log.append(("send", msg["To"], msg["From"], msg["Subject"]))
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_user", "me@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    ok, _ = asyncio.run(alerts.send("Highlightz: x", "body"))
    assert ok
    assert log == [("connect", "smtp.gmail.com", 587), ("starttls", True), ("login", "me@gmail.com"),
                   ("send", "owner@example.com", "me@gmail.com", "Highlightz: x")]


def test_smtp_on_465_checks_the_certificate_too(monkeypatch):
    import smtplib
    seen = {}

    class _SSL:
        def __init__(self, host, port, timeout=None, context=None): seen["verified"] = _verifies(context)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): pass
        def send_message(self, msg): seen["sent"] = True
    monkeypatch.setattr(smtplib, "SMTP_SSL", _SSL)
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_user", "me@gmail.com")
    ok, _ = asyncio.run(alerts.send("s", "b"))
    assert ok and seen == {"verified": True, "sent": True}


def test_alerts_go_to_the_business_inbox_unless_told_otherwise(monkeypatch, tmp_path):
    """Owner, 2026-10-20: "make it send to business@highlightz.app". Read from
    real .env files: no line → that inbox, an address → that address, an
    empty line → off."""
    from config.settings import Settings
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)

    def to(name, env):
        (tmp_path / name).write_text(env)
        return Settings(_env_file=tmp_path / name).alert_email_to

    assert to("none.env", "# nothing\n") == "business@highlightz.app"
    assert to("set.env", "ALERT_EMAIL_TO=me@example.com\n") == "me@example.com"
    assert to("empty.env", "ALERT_EMAIL_TO=\n") == ""
    monkeypatch.setattr(settings, "alert_email_to", "")
    assert not alerts.configured()
    ok, why = asyncio.run(alerts.send("s", "t"))
    assert not ok and "ALERT_EMAIL_TO is empty" in why


def test_the_test_command_says_why_it_did_not_send(monkeypatch, capsys):
    from src.alerts import __main__ as cli
    monkeypatch.setattr(settings, "resend_api_key", "")
    assert cli.main() == 1
    assert "NOT sent: no way to send" in capsys.readouterr().out
