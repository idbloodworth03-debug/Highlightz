"""What security is in place on this server and site, as they are right now.

    cd /opt/highlightz && venv/bin/python scripts/security_check.py

Owner, 2026-11-18: "what security measures do i have in place currently on my
server, site and my github". The code and GitHub can be read from anywhere;
the SERVER can only be read on the server, and the notes about it
(docs/HANDOFF.md: firewall, TLS, backups) are as old as the day somebody last
looked. This looks again.

READ-ONLY. It runs the system's own read commands (ufw status, ss, sshd -T,
systemctl show, redis-cli config get, openssl x509) and reads a few files. It
changes nothing, restarts nothing, and tries no login.

WHAT IT PRINTS: yes / no, counts, port numbers, program names, dates. NEVER
an address (a listening socket is "this machine only" or "the internet", not
its IP), never a key, password, token or setting's value, never a user's
name, never a command line. Safe to paste.

Each line that is a problem ends with "<-" and why. A check whose tool is not
on the machine says "could not tell" rather than guessing; run it as root or
most of them will.

WHAT IT CANNOT SEE, listed at the end: anything that lives in an account
rather than on this machine (DigitalOcean, GitHub, Stripe, Google, Twitch).
"""
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNIT = "highlightz"
EXPECTED_PUBLIC_PORTS = {22, 80, 443}
problems: list[str] = []


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    """(exit code, output). -1 and "" when the program is not there or hangs."""
    try:
        got = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
        return got.returncode, (got.stdout or "") + (got.stderr or "")
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return -1, ""


def is_active(unit: str) -> bool | None:
    """Is this systemd unit running? None when systemd cannot be asked (not
    root, no systemd): unknown is never read as "no"."""
    word = run(["systemctl", "is-active", unit])[1].strip().splitlines()[:1]
    word = word[0].strip() if word else ""
    if word == "active":
        return True
    return False if word in ("inactive", "failed", "activating", "deactivating") else None


def say(label: str, value: str, problem: str = "") -> None:
    print(f"  {label:<30}: {value}" + (f"   <- {problem}" if problem else ""))
    if problem:
        problems.append(f"{label}: {problem}")


# ── parsers, each of one command's output ───────────────────────────────────

def parse_apt_check(text: str) -> tuple[int, int] | None:
    """`apt-check` prints "updates;security"."""
    m = re.search(r"(\d+);(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_ufw(text: str) -> dict:
    """{"active": bool|None, "ports": sorted port numbers allowed in from
    anywhere, "narrowed": how many rules allow only particular sources}.
    A source address is counted, never kept."""
    if not text.strip():
        return {"active": None, "ports": [], "narrowed": 0}
    active = bool(re.search(r"^Status:\s*active", text, re.M))
    ports, narrowed = set(), 0
    for line in text.splitlines():
        m = re.match(r"^(.+?)\s+(ALLOW|LIMIT)(?:\s+IN)?\s+(.+)$", line.strip())
        if not m:
            continue
        # "22/tcp", "443", "22/tcp (v6)", or a named profile with a space in
        # it ("Nginx Full").
        what, source = m.group(1).replace("(v6)", "").strip(), m.group(3).strip()
        if not source.startswith("Anywhere"):
            narrowed += 1
            continue
        for p in re.findall(r"\d+", what.split("/")[0]):
            ports.add(int(p))
        if not re.search(r"\d", what):                     # a named profile: "OpenSSH", "Nginx Full"
            ports.update({"openssh": [22], "nginx full": [80, 443], "nginx http": [80],
                          "nginx https": [443]}.get(what.lower(), []))
    return {"active": active, "ports": sorted(ports), "narrowed": narrowed}


def parse_listeners(text: str) -> list[dict]:
    """`ss -H -tlnp` -> [{"port", "program", "reach"}], one per port and reach.
    reach is "this machine only" or "the internet"; the address is dropped."""
    seen, out = set(), []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        host, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        host = host.strip("[]")
        host = host.split("%")[0]
        local_only = host.startswith("127.") or host in ("::1", "localhost")
        m = re.search(r'users:\(\("([^"]+)"', line)
        row = (int(port), m.group(1) if m else "?", "this machine only" if local_only else "the internet")
        if row not in seen:
            seen.add(row)
            out.append({"port": row[0], "program": row[1], "reach": row[2]})
    return sorted(out, key=lambda r: (r["reach"], r["port"]))


def parse_sshd(text: str) -> dict:
    """`sshd -T` -> the handful of settings that decide who can get in."""
    want = ("permitrootlogin", "passwordauthentication", "kbdinteractiveauthentication",
            "pubkeyauthentication", "port", "maxauthtries")
    out = {}
    for line in (text or "").splitlines():
        key, _, value = line.strip().partition(" ")
        if key.lower() in want:
            out[key.lower()] = value.strip().lower()
    return out


def cert_days_left(not_after: str, now: float | None = None) -> float | None:
    """`openssl x509 -enddate` prints "notAfter=Feb  3 12:00:00 2027 GMT"."""
    m = re.search(r"notAfter=(.+)", not_after or "")
    if not m:
        return None
    try:
        end = time.mktime(time.strptime(m.group(1).strip().replace(" GMT", ""), "%b %d %H:%M:%S %Y")) - time.timezone
    except ValueError:
        return None
    return (end - (time.time() if now is None else now)) / 86400.0


def mode_of(path: pathlib.Path) -> tuple[str, bool] | None:
    """("640 root:highlightz", readable by everybody?) or None if not there."""
    try:
        st = path.stat()
    except OSError:
        return None
    try:
        import grp
        import pwd
        who = f"{pwd.getpwuid(st.st_uid).pw_name}:{grp.getgrgid(st.st_gid).gr_name}"
    except (KeyError, ImportError):
        who = f"{st.st_uid}:{st.st_gid}"
    return f"{stat.S_IMODE(st.st_mode):o} {who}", bool(st.st_mode & stat.S_IROTH)


# ── the checks ───────────────────────────────────────────────────────────────

def the_server() -> None:
    print("=" * 74)
    print("THE SERVER")
    print("=" * 74)
    name = ""
    try:
        for line in pathlib.Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                name = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    say("system", name or "could not tell")
    say("restart waiting", "yes" if pathlib.Path("/var/run/reboot-required").exists() else "no",
        "an update is installed but not in effect until the server reboots"
        if pathlib.Path("/var/run/reboot-required").exists() else "")
    got = parse_apt_check(run(["/usr/lib/update-notifier/apt-check"], 60)[1])
    if got is None:
        say("updates waiting", "could not tell")
    else:
        say("updates waiting", f"{got[0]}, of them security: {got[1]}",
            "security updates are not installed" if got[1] else "")
    auto = ""
    try:
        auto = pathlib.Path("/etc/apt/apt.conf.d/20auto-upgrades").read_text()
    except OSError:
        pass
    on = bool(re.search(r'Unattended-Upgrade\s+"1"', auto)) and bool(shutil.which("unattended-upgrade"))
    say("security updates by itself", "yes" if on else "no",
        "" if on else "nobody installs security fixes unless you do it by hand")

    ufw = parse_ufw(run(["ufw", "status"])[1])
    if ufw["active"] is None:
        say("firewall (ufw)", "could not tell (run this as root)")
    elif not ufw["active"]:
        say("firewall (ufw)", "OFF", "every program that listens is reachable from the internet")
    else:
        extra = sorted(set(ufw["ports"]) - EXPECTED_PUBLIC_PORTS)
        say("firewall (ufw)", "on, lets in: " + (", ".join(map(str, ufw["ports"])) or "nothing")
            + (f" (+{ufw['narrowed']} rule(s) for particular sources)" if ufw["narrowed"] else ""),
            f"more than SSH and the website is open: {', '.join(map(str, extra))}" if extra else "")

    rows = parse_listeners(run(["ss", "-H", "-tlnp"])[1])
    if not rows:
        say("programs listening", "could not tell")
    for r in rows:
        bad = ""
        if r["reach"] == "the internet" and r["port"] not in EXPECTED_PUBLIC_PORTS:
            blocked = ufw["active"] and r["port"] not in ufw["ports"]
            bad = ("" if blocked else
                   "listening to the internet and the firewall does not stop it")
            note = " (the firewall blocks it)" if blocked else ""
        else:
            note = ""
        say(f"port {r['port']} ({r['program'][:14]})", r["reach"] + note, bad)

    ssh = parse_sshd(run(["sshd", "-T"])[1])
    if not ssh:
        say("SSH settings", "could not tell (run this as root)")
    else:
        pw = ssh.get("passwordauthentication", "?")
        say("SSH: password logins", pw,
            "anyone can try passwords; keys only is the safe setting" if pw == "yes" else "")
        root = ssh.get("permitrootlogin", "?")
        say("SSH: root may log in", root,
            "root with a password is the login every scanner tries first"
            if root == "yes" and pw == "yes" else "")
        say("SSH: port", ssh.get("port", "?"))
    try:
        keys = [ln for ln in pathlib.Path("/root/.ssh/authorized_keys").read_text().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        say("SSH: keys that open root", str(len(keys)),
            "" if len(keys) <= 3 else "more keys than people; check each one is still yours")
    except OSError:
        say("SSH: keys that open root", "none found, or could not read")
    f2b = is_active("fail2ban")
    say("fail2ban (bans repeat guessers)", "could not tell" if f2b is None else "on" if f2b else "off",
        "nothing slows down password guessing on SSH" if f2b is False else "")
    rc, log = run(["journalctl", "-u", "ssh", "-u", "sshd", "--since", "-24h", "--no-pager", "-o", "cat"], 60)
    if rc == 0:
        say("SSH: failed tries, last 24h", f"{len(re.findall(r'Failed password|Invalid user', log)):,}")

    rc, show = run(["systemctl", "show", UNIT, "-p", "User", "-p", "NoNewPrivileges",
                    "-p", "ProtectSystem", "-p", "PrivateTmp", "-p", "ActiveState"])
    props = dict(ln.split("=", 1) for ln in show.splitlines() if "=" in ln) if rc == 0 else {}
    if not props:
        say("the site's service", "could not tell")
    else:
        user = props.get("User") or "root"
        say("the site runs as", "an unprivileged user" if user != "root" else "ROOT",
            "" if user != "root" else "a bug in the site would be a bug with root's powers")
        hard = [k for k, want in (("NoNewPrivileges", "yes"), ("PrivateTmp", "yes"), ("ProtectSystem", "strict"))
                if props.get(k) != want]
        say("service lock-down", "all three on" if not hard else "missing: " + ", ".join(hard),
            "" if not hard else "the installed unit is not the hardened one in deploy/")

    env = mode_of(ROOT / ".env")
    if env is None:
        say(".env (every secret)", "not found here")
    else:
        say(".env (every secret)", env[0], "readable by every user on the machine" if env[1] else "")
    rc, url = run(["git", "-C", str(ROOT), "remote", "get-url", "origin"])
    if rc == 0:
        say("git: password in the remote", "yes" if re.search(r"://[^/@\s]+@", url) else "no",
            "a GitHub token is stored in plain text in .git/config" if re.search(r"://[^/@\s]+:[^/@\s]+@", url) else "")
        anon = url.strip().startswith("https://github.com/") and "@" not in url
        say("git: pulls without a login", "yes" if anon else "no",
            "that only works for a PUBLIC repository: anyone can read the code and docs/HANDOFF.md"
            if anon else "")

    rc, bind = run(["redis-cli", "config", "get", "bind"])
    rc2, pw = run(["redis-cli", "config", "get", "requirepass"])
    if rc == 0 and bind.strip():
        hosts = bind.split()[1:] if len(bind.split()) > 1 else []
        local = bool(hosts) and all(h.lstrip("-").startswith("127.") or h.lstrip("-") in ("::1", "localhost") for h in hosts)
        has_pw = rc2 == 0 and len(pw.split()) > 1 and bool(pw.split()[1].strip('"'))
        say("Redis", ("this machine only" if local else "LISTENS BEYOND THIS MACHINE")
            + (", with a password" if has_pw else ", no password"),
            "" if local else "the clip queue is reachable from outside unless the firewall stops it")
    else:
        say("Redis", "could not tell")

    certs = sorted(pathlib.Path("/etc/letsencrypt/live").glob("*/cert.pem")) \
        if pathlib.Path("/etc/letsencrypt/live").is_dir() else []
    if not certs:
        say("HTTPS certificate", "none found by this user")
    for c in certs[:3]:
        days = cert_days_left(run(["openssl", "x509", "-enddate", "-noout", "-in", str(c)])[1])
        say(f"certificate {c.parent.name[:20]}", "could not read" if days is None else f"{days:.0f} days left",
            "it is not being renewed" if days is not None and days < 14 else "")
    timers = [is_active(t) for t in ("certbot.timer", "snap.certbot.renew.timer")]
    if any(timers):
        say("certificate renews by itself", "yes")
    elif all(t is None for t in timers):
        say("certificate renews by itself", "could not tell")
    else:
        say("certificate renews by itself", "no",
            "the site's HTTPS stops working when the certificate runs out")

    backups = ROOT / "backups"
    archives = sorted(backups.glob("highlightz-state-*.tar.gz"), key=lambda p: p.stat().st_mtime) \
        if backups.is_dir() else []
    if not archives:
        say("backups on this server", "none found", "losing the disk would lose every account")
    else:
        age_h = (time.time() - archives[-1].stat().st_mtime) / 3600.0
        say("backups on this server", f"{len(archives)} kept, newest {age_h:.0f} hours old",
            "the nightly backup has stopped" if age_h > 36 else "")


def the_site() -> None:
    print()
    print("=" * 74)
    print("THE SITE, AS IT IS SET UP HERE")
    print("=" * 74)
    try:
        from config import settings as cfg
        from config.settings import settings
    except Exception:
        say("settings", "could not be read from here")
        return
    env_text = ""
    try:
        env_text = (ROOT / ".env").read_text()
    except OSError:
        pass

    def given(key: str) -> bool:
        return bool(re.search(rf"^{key}=\S", env_text, re.M))

    say("session signing key", "your own" if given("DASHBOARD_SECRET_KEY") else "NOT SET",
        "" if given("DASHBOARD_SECRET_KEY") else "a new one is made at every restart, signing everybody out")
    default_pw = settings.dashboard_password == getattr(cfg, "_DEFAULT_PASSWORD", object())
    say("dashboard password", "the DEFAULT" if default_pw else "your own",
        "the default is printed in the public source code" if default_pw else "")
    say("cookies over HTTPS only", "yes" if settings.dashboard_https_only else "NO",
        "" if settings.dashboard_https_only else "a sign-in cookie can travel unencrypted")
    say("stored tokens' own key", "yes" if settings.token_encryption_key else "no",
        "" if settings.token_encryption_key else "tokens are encrypted with the session key instead of one of their own")
    if getattr(settings, "stripe_secret_key", ""):
        ok = bool(getattr(settings, "stripe_webhook_secret", ""))
        say("Stripe messages are verified", "yes" if ok else "NO",
            "" if ok else "anybody could tell the site that somebody paid")
    say("off-site backup", "set up" if getattr(settings, "backup_s3_bucket", "") else "not set up",
        "" if getattr(settings, "backup_s3_bucket", "") else "every backup is on the same disk as the data")
    try:
        from src.auth import users as user_store
        everyone = user_store.get_all()
        admins = sum(1 for u in everyone if u.get("is_admin"))
        with_pw = sum(1 for u in everyone if u.get("password_hash"))
        say("admin accounts", str(admins), "" if admins <= 3 else "each one can see every customer; check all are needed")
        say("accounts with a password", f"{with_pw} of {len(everyone)}   (the rest sign in through Twitch or Kick)")
    except Exception:
        say("accounts", "could not be read from here")
    rc, head = run(["curl", "-sI", "-m", "10", f"http://127.0.0.1:{getattr(settings, 'dashboard_port', 8000)}/login"])
    if rc == 0 and head:
        have = {h.split(":", 1)[0].strip().lower() for h in head.splitlines() if ":" in h}
        want = ("content-security-policy", "x-frame-options", "x-content-type-options",
                "strict-transport-security", "referrer-policy")
        missing = [w for w in want if w not in have]
        say("protective headers sent", "all five" if not missing else "missing: " + ", ".join(missing),
            "" if not missing else "the running site is not sending them")
    else:
        say("protective headers sent", "could not ask the running site")


def main(argv: list[str]) -> int:
    the_server()
    the_site()
    print()
    print("=" * 74)
    print("WHAT THIS CANNOT SEE  (it lives in an account, not on this machine)")
    print("=" * 74)
    for line in ("DigitalOcean: two-step sign-in, who else is on the team, the cloud firewall,",
                 "  and whether droplet backups or snapshots are on.",
                 "GitHub: two-step sign-in on each account that can push, and the repository's",
                 "  visibility and branch rules.",
                 "Stripe, Google, Twitch, Kick and your email: two-step sign-in on each."):
        print("  " + line)
    print()
    if problems:
        print(f"  {len(problems)} THING(S) TO LOOK AT:")
        for p in problems:
            print("   - " + p)
    else:
        print("  NOTHING HERE NEEDS ATTENTION. (Lines that say \"could not tell\" were not checked.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
