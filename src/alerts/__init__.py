"""Email when something breaks (owner, 2026-10-20: "alerts to email").

WHY. Two real failures ran for days before a customer noticed: the Stripe
webhook answered 500 to every delivery for a week (2026-10-13), and Kick
refused every chat subscription, 733 times in one day (2026-10-15). Both were
in the journal, which nobody reads — and the log lines carried no level, so an
error looked like any other line.

WHAT COUNTS.
  - every error: log.error / log.exception, and stdlib ERROR records such as
    uvicorn's tracebacks and asyncio's "Task exception was never retrieved";
  - `request_failed` — an unhandled exception inside a request, logged as a
    warning by RequestFailureLogMiddleware, and always a bug;
  - any other warning that repeats `alert_warning_burst` times within an hour
    (the Kick incident was one warning, over and over).
  Events named in ALERT_IGNORE never count, and the alerter's own never do.

HOW OFTEN. One check a minute; at most one email per check, listing every
event that crossed the line. The same event is not mentioned again for
`alert_cooldown_minutes` (6 h), and there are never more than
`alert_max_per_day` emails a day: an alert storm must not become an email
storm, or the owner learns to ignore them.

HOW IT SENDS. RESEND_API_KEY → Resend's HTTPS API (DigitalOcean blocks
outgoing SMTP on most droplets; HTTPS is never blocked). Otherwise SMTP_HOST →
smtplib, on a worker thread. Neither → nothing is sent, and that is said once.
`python -m src.alerts` sends a test email and prints what happened.

WHAT IT NEVER DOES: raise into the code that logged, block the event loop, or
put a secret in an email — fields whose names look secret are dropped and every
value is cut short.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import socket
import sys
import time
from dataclasses import dataclass, field

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

_SELF = "alert_"                     # the alerter's own events never alert
_ALWAYS = frozenset({"request_failed"})
_WINDOW_S = 3600
_MAX_EVENTS = 500                    # distinct event names tracked at once
_RETRY_S = 600                       # after a failed send
_SECRETISH = ("token", "secret", "passw", "authoriz", "cookie", "signature",
              "email", "api_key", "apikey", "private")
_SECRET_NAMES = frozenset({"key", "code", "auth", "sig"})
_LEVELS = {"warning": 1, "error": 2, "critical": 3}


@dataclass
class _Seen:
    level: str
    count: int = 0                   # since the last email that named it
    first: float = 0.0
    last: float = 0.0
    example: dict = field(default_factory=dict)
    recent: collections.deque = field(default_factory=lambda: collections.deque(maxlen=1000))
    alerted_at: float = 0.0


_seen: dict[str, _Seen] = {}
_sent: list[float] = []              # when each email went, for the daily cap
_next_try = 0.0
_said_unconfigured = False


def _ignored() -> set[str]:
    return {s.strip() for s in (settings.alert_ignore or "").split(",") if s.strip()}


def _clean(fields: dict) -> dict:
    out = {}
    for k, v in list(fields.items())[:12]:
        name = str(k).lower()
        if name in _SECRET_NAMES or any(s in name for s in _SECRETISH):
            continue
        text = str(v)
        if name == "exception":
            # A whole traceback: its last line says what went wrong.
            text = next((ln for ln in reversed(text.splitlines()) if ln.strip()), text)
        out[str(k)[:40]] = text[:200]
    return out


def note(event: str, level: str, fields: dict | None = None, now: float | None = None) -> None:
    """Count one warning or error. Never raises."""
    try:
        if not event or event.startswith(_SELF) or event in _ignored():
            return
        now = time.time() if now is None else now
        s = _seen.get(event)
        if s is None:
            if len(_seen) >= _MAX_EVENTS:
                return
            s = _seen[event] = _Seen(level=level, first=now)
        if _LEVELS.get(level, 0) > _LEVELS.get(s.level, 0):
            s.level = level
        s.count += 1
        s.last = now
        s.recent.append(now)
        s.example = _clean(fields or {})
    except Exception:
        pass


def processor(logger, method_name, event_dict):
    """structlog processor: notes warnings and errors, passes everything on."""
    try:
        level = event_dict.get("level") or method_name
        level = {"warn": "warning", "exception": "error", "fatal": "critical"}.get(level, level)
        if level in _LEVELS:
            note(str(event_dict.get("event") or ""), level,
                 {k: v for k, v in event_dict.items() if k not in ("event", "level", "timestamp")})
    except Exception:
        pass
    return event_dict


class _Tap(logging.Handler):
    """Hands stdlib ERROR records to note(). With echo it also writes every
    record it gets to stderr, as Python's last-resort handler would have:
    attaching any handler to the root logger silences that fallback, and those
    lines must still reach the journal."""

    def __init__(self, echo: bool):
        super().__init__(level=logging.WARNING if echo else logging.ERROR)
        self.echo = echo
        self.setFormatter(logging.Formatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno >= logging.ERROR:
                first = (record.getMessage() or "").splitlines()
                fields = {"message": first[0] if first else ""}
                if record.exc_info and record.exc_info[1] is not None:
                    e = record.exc_info[1]
                    fields["exception"] = f"{type(e).__name__}: {e}"
                note(f"stdlib:{record.name}", "error", fields)
            if self.echo:
                sys.stderr.write(self.format(record) + "\n")
        except Exception:
            pass


def install_stdlib_taps() -> None:
    """Catch stdlib ERRORs that propagate to the root logger (asyncio's lost
    task exceptions, this app's few stdlib loggers)."""
    root = logging.getLogger()
    if not any(isinstance(h, _Tap) for h in root.handlers):
        root.addHandler(_Tap(echo=not root.handlers))


def install_uvicorn_tap() -> None:
    """uvicorn's error logger does not propagate to the root. Call AFTER
    uvicorn.Config is built: building it re-applies uvicorn's logging config,
    which would drop a handler added earlier."""
    lg = logging.getLogger("uvicorn.error")
    if not any(isinstance(h, _Tap) for h in lg.handlers):
        lg.addHandler(_Tap(echo=False))


# ── deciding ─────────────────────────────────────────────────────────────────

def due(now: float | None = None) -> list[tuple[str, _Seen]]:
    """The events that should be in the next email."""
    now = time.time() if now is None else now
    cool = settings.alert_cooldown_minutes * 60
    out = []
    for ev, s in _seen.items():
        while s.recent and now - s.recent[0] > _WINDOW_S:
            s.recent.popleft()
        if s.count == 0 or (s.alerted_at and now - s.alerted_at < cool):
            continue
        if _LEVELS.get(s.level, 0) >= _LEVELS["error"] or ev in _ALWAYS \
                or len(s.recent) >= settings.alert_warning_burst:
            out.append((ev, s))
    out.sort(key=lambda x: (-_LEVELS.get(x[1].level, 0), -x[1].count))
    return out


def _when(ts: float) -> str:
    return time.strftime("%b %d %H:%M UTC", time.gmtime(ts))


def compose(items: list[tuple[str, _Seen]], now: float | None = None) -> tuple[str, str]:
    now = time.time() if now is None else now
    head = items[0][0] + (f" and {len(items) - 1} more" if len(items) > 1 else "")
    subject = f"Highlightz: {head}"
    lines = ["Something on highlightz.app needs a look.", ""]
    for ev, s in items[:20]:
        burst = len(s.recent)
        lines.append(f"{ev} — {s.level}, {s.count} time{'s' if s.count != 1 else ''}"
                     f" (first {_when(s.first)}, last {_when(s.last)}"
                     + (f"; {burst} in the last hour" if s.level == 'warning' else "") + ")")
        for k, v in s.example.items():
            lines.append(f"    {k}: {v}")
        lines.append("")
    if len(items) > 20:
        lines += [f"…and {len(items) - 20} more.", ""]
    first = items[0][0].replace("'", "")
    lines += [
        "To see the full log lines, on the server:",
        f"    journalctl -u highlightz --since \"3 hours ago\" -o cat | grep '{first}'",
        "",
        f"Server: {socket.gethostname()}. At most one email a minute, each problem "
        f"named once every {settings.alert_cooldown_minutes // 60} h, at most "
        f"{settings.alert_max_per_day} emails a day. To silence one, add its name "
        "to ALERT_IGNORE in .env.",
    ]
    return subject, "\n".join(lines)


# ── sending ──────────────────────────────────────────────────────────────────

def configured() -> bool:
    return bool(settings.alert_email_to.strip()
                and (settings.resend_api_key or settings.smtp_host))


async def send(subject: str, text: str) -> tuple[bool, str]:
    """(sent, what happened). Never raises."""
    # A subject is one line. A line break in one (from a title someone typed)
    # made the SMTP path raise outside its try, and may make Resend refuse it.
    subject = " ".join(str(subject).split())
    to = settings.alert_email_to.strip()
    if not to:
        return False, "ALERT_EMAIL_TO is empty in .env (delete the line to use the default)"
    if settings.resend_api_key:
        return await _send_resend(to, subject, text)
    if settings.smtp_host:
        return await asyncio.to_thread(_send_smtp, to, subject, text)
    return False, "no way to send: set RESEND_API_KEY (or SMTP_HOST, SMTP_USER, SMTP_PASSWORD)"


async def _send_resend(to: str, subject: str, text: str) -> tuple[bool, str]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={"from": settings.alert_email_from, "to": [to],
                      "subject": subject, "text": text})
    except Exception as exc:
        return False, f"could not reach Resend: {type(exc).__name__}: {exc}"
    if r.status_code in (200, 201, 202):
        return True, "sent"
    return False, f"Resend answered HTTP {r.status_code}: {r.text[:200]}"


def _send_smtp(to: str, subject: str, text: str) -> tuple[bool, str]:
    import smtplib
    import ssl
    from email.message import EmailMessage
    sender = settings.alert_email_from
    if "resend.dev" in sender:
        # The default only works through Resend; over SMTP the account sends.
        sender = settings.smtp_user or sender
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(text)
    # A context that checks the server's certificate. smtplib's own default
    # does not, and this connection carries the mail account's password.
    tls = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=20, context=tls) as s:
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
                s.starttls(context=tls)
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
    except Exception as exc:
        return False, f"SMTP failed: {type(exc).__name__}: {exc}"
    return True, "sent"


async def check_and_send(now: float | None = None) -> bool:
    """One pass: email what is due, if anything is and it may. True if sent."""
    global _next_try, _said_unconfigured
    now = time.time() if now is None else now
    items = due(now)
    if not items or now < _next_try:
        return False
    if not configured():
        if not _said_unconfigured:
            _said_unconfigured = True
            log.warning("alert_email_unconfigured",
                        waiting=[ev for ev, _ in items][:10],
                        hint="set RESEND_API_KEY (or SMTP_HOST, SMTP_USER, SMTP_PASSWORD) in .env")
        return False
    _sent[:] = [t for t in _sent if now - t < 86400]
    if len(_sent) >= settings.alert_max_per_day:
        return False
    subject, text = compose(items, now)
    ok, why = await send(subject, text)
    if not ok:
        _next_try = now + _RETRY_S
        log.warning("alert_send_failed", reason=why)
        return False
    for _, s in items:
        s.alerted_at, s.count = now, 0
        s.recent.clear()
    _sent.append(now)
    log.info("alert_sent", events=[ev for ev, _ in items][:20])
    return True


async def run(interval_s: float = 60.0) -> None:
    """The alert loop. Never raises: it runs in main's gather, where an
    exception would take the whole process down with it."""
    while True:
        try:
            await asyncio.sleep(interval_s)
            await check_and_send()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                log.warning("alert_loop_error", error=str(exc))
            except Exception:
                pass
