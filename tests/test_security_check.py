"""scripts/security_check.py: what security is in place on the server, now.

Owner, 2026-11-18: "what security measures do i have in place currently on my
server, site and my github". Held here: each command's output is read the way
that command writes it; a problem is called one; and the report can be pasted
because it never carries an address, a key or a name.
"""

import importlib.util
import pathlib
import time

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "security_check.py"
spec = importlib.util.spec_from_file_location("security_check", _PATH)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

UFW = """Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
80/tcp                     ALLOW       Anywhere
443                        ALLOW       Anywhere
6379                       ALLOW       203.0.113.77
22/tcp (v6)                ALLOW       Anywhere (v6)
80/tcp (v6)                ALLOW       Anywhere (v6)
"""

SS = """LISTEN 0      511          0.0.0.0:80        0.0.0.0:*    users:(("nginx",pid=812,fd=6),("nginx",pid=811,fd=6))
LISTEN 0      511          0.0.0.0:443       0.0.0.0:*    users:(("nginx",pid=812,fd=7))
LISTEN 0      4096       127.0.0.1:8000      0.0.0.0:*    users:(("python",pid=991,fd=14))
LISTEN 0      511        127.0.0.1:6379      0.0.0.0:*    users:(("redis-server",pid=700,fd=6))
LISTEN 0      128          0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=650,fd=3))
LISTEN 0      128             [::]:22           [::]:*    users:(("sshd",pid=650,fd=4))
LISTEN 0      511            [::1]:6379         [::]:*    users:(("redis-server",pid=700,fd=7))
LISTEN 0      100    198.51.100.23:9100      0.0.0.0:*    users:(("node_exporter",pid=77,fd=3))
"""


def test_the_firewall_is_read_as_ufw_writes_it():
    got = S.parse_ufw(UFW)
    assert got == {"active": True, "ports": [22, 80, 443], "narrowed": 1}
    assert S.parse_ufw("Status: inactive\n") == {"active": False, "ports": [], "narrowed": 0}
    assert S.parse_ufw("")["active"] is None                      # not run as root: unknown, not "off"
    named = S.parse_ufw("Status: active\n\nOpenSSH    ALLOW   Anywhere\nNginx Full  ALLOW  Anywhere\n")
    assert named["ports"] == [22, 80, 443]


def test_what_is_listening_says_how_far_it_reaches_and_never_where():
    rows = S.parse_listeners(SS)
    reach = {(r["port"], r["program"]): r["reach"] for r in rows}
    assert reach[(8000, "python")] == "this machine only"
    assert reach[(6379, "redis-server")] == "this machine only"
    assert reach[(443, "nginx")] == "the internet" and reach[(22, "sshd")] == "the internet"
    assert reach[(9100, "node_exporter")] == "the internet"       # one outside address is outside
    assert len([r for r in rows if r["port"] == 22]) == 1         # v4 and v6 are one line
    assert "198.51.100.23" not in repr(rows) and "0.0.0.0" not in repr(rows)
    assert S.parse_listeners("") == [] and S.parse_listeners("garbage\n") == []


def test_ssh_settings_are_the_ones_that_decide_who_gets_in():
    got = S.parse_sshd("port 22\npermitrootlogin yes\npasswordauthentication no\n"
                       "pubkeyauthentication yes\nx11forwarding no\nmaxauthtries 6\n")
    assert got == {"port": "22", "permitrootlogin": "yes", "passwordauthentication": "no",
                   "pubkeyauthentication": "yes", "maxauthtries": "6"}
    assert S.parse_sshd("") == {}


def test_waiting_updates_and_the_certificates_days():
    assert S.parse_apt_check("12;3") == (12, 3) and S.parse_apt_check("") is None
    now = time.mktime(time.strptime("Jan 04 12:00:00 2027", "%b %d %H:%M:%S %Y")) - time.timezone
    assert S.cert_days_left("notAfter=Feb  3 12:00:00 2027 GMT", now) == pytest.approx(30.0, abs=0.01)
    assert S.cert_days_left("unable to load certificate") is None


def test_a_file_everybody_can_read_is_said_to_be(tmp_path):
    f = tmp_path / ".env"
    f.write_text("SECRET=1\n")
    f.chmod(0o640)
    assert S.mode_of(f)[0].startswith("640 ") and S.mode_of(f)[1] is False
    f.chmod(0o644)
    assert S.mode_of(f)[1] is True
    assert S.mode_of(tmp_path / "nope") is None


def _fake(monkeypatch, answers):
    def run(cmd, timeout=20):
        for key, out in answers.items():
            if " ".join(cmd).startswith(key):
                return 0, out
        return -1, ""
    monkeypatch.setattr(S, "run", run)
    monkeypatch.setattr(S, "problems", [])


def test_a_healthy_server_reads_clean_and_a_weak_one_is_named(monkeypatch, capsys):
    good = {"ufw status": UFW, "ss -H -tlnp": SS.replace("198.51.100.23:9100", "127.0.0.1:9100"),
            "sshd -T": "port 22\npermitrootlogin prohibit-password\npasswordauthentication no\n",
            "systemctl is-active fail2ban": "active\n",
            "systemctl show": "User=highlightz\nNoNewPrivileges=yes\nProtectSystem=strict\nPrivateTmp=yes\n",
            "redis-cli config get bind": "bind\n127.0.0.1 -::1\n",
            "redis-cli config get requirepass": "requirepass\n\n",
            "systemctl is-active certbot.timer": "active\n",
            "/usr/lib/update-notifier/apt-check": "4;0"}
    _fake(monkeypatch, good)
    S.the_server()
    out = capsys.readouterr().out
    assert "firewall (ufw)                : on, lets in: 22, 80, 443 (+1 rule(s) for particular sources)" in out
    assert "port 8000 (python)            : this machine only" in out
    assert "SSH: password logins          : no" in out
    assert "the site runs as              : an unprivileged user" in out
    assert "service lock-down             : all three on" in out
    assert "Redis                         : this machine only, no password" in out
    # What is left depends on this machine's own files (no backups folder, no
    # auto-update file, the repository's public remote), not on the answers above.
    flagged = [p for p in S.problems if not p.startswith(("security updates by itself", "backups on this server",
                                                          "certificate", "restart waiting", "git: pulls"))]
    assert flagged == [], flagged
    assert "fail2ban (bans repeat guessers): on" in out and "certificate renews by itself  : yes" in out

    bad = dict(good, **{"ufw status": "Status: inactive\n", "ss -H -tlnp": SS,
                        "sshd -T": "port 22\npermitrootlogin yes\npasswordauthentication yes\n",
                        "systemctl is-active fail2ban": "inactive\n",
                        "systemctl is-active certbot.timer": "inactive\n",
                        "systemctl is-active snap.certbot.renew.timer": "inactive\n",
                        "systemctl show": "User=\nNoNewPrivileges=no\nProtectSystem=no\nPrivateTmp=no\n",
                        "redis-cli config get bind": "bind\n0.0.0.0\n",
                        "/usr/lib/update-notifier/apt-check": "30;7"})
    _fake(monkeypatch, bad)
    S.the_server()
    out = capsys.readouterr().out
    said = " | ".join(S.problems)
    for must in ("firewall (ufw): every program that listens is reachable",
                 "port 9100 (node_exporter): listening to the internet and the firewall does not stop it",
                 "SSH: password logins: anyone can try passwords",
                 "SSH: root may log in: root with a password",
                 "fail2ban (bans repeat guessers): nothing slows down",
                 "certificate renews by itself: the site's HTTPS stops working",
                 "the site runs as: a bug in the site would be a bug with root's powers",
                 "service lock-down: the installed unit is not the hardened one",
                 "Redis: the clip queue is reachable from outside",
                 "updates waiting: security updates are not installed"):
        assert must in said, must
    # A port open to the world that the firewall blocks is not a problem.
    _fake(monkeypatch, dict(good, **{"ss -H -tlnp": SS}))
    S.the_server()
    out = capsys.readouterr().out
    assert "port 9100 (node_exporter)     : the internet (the firewall blocks it)" in out
    assert not any("9100" in p for p in S.problems)
    for private in ("198.51.100.23", "203.0.113.77", "0.0.0.0", "127.0.0.1"):
        assert private not in out, private


def test_the_sites_settings_are_yes_or_no_and_never_the_value(monkeypatch, capsys, tmp_path):
    from config.settings import settings
    (tmp_path / ".env").write_text("DASHBOARD_SECRET_KEY=SUPER-SECRET-VALUE-1234\nTOKEN_ENCRYPTION_KEY=TOKEN-KEY-9\n")
    monkeypatch.setattr(S, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "dashboard_password", "a-real-password-XYZ")
    monkeypatch.setattr(settings, "token_encryption_key", "TOKEN-KEY-9")
    monkeypatch.setattr(settings, "dashboard_https_only", True)
    monkeypatch.setattr("src.auth.users.get_all", lambda: [
        {"id": "a", "username": "the-boss", "is_admin": True, "password_hash": "h"},
        {"id": "b", "username": "someone", "email": "someone@example.com"}])
    _fake(monkeypatch, {"curl -sI": "HTTP/1.1 200 OK\ncontent-security-policy: x\nx-frame-options: DENY\n"
                                    "x-content-type-options: nosniff\nreferrer-policy: x\n"})
    S.the_site()
    out = capsys.readouterr().out
    assert "session signing key           : your own" in out
    assert "dashboard password            : your own" in out
    assert "stored tokens' own key        : yes" in out
    assert "admin accounts                : 1" in out and "accounts with a password      : 1 of 2" in out
    assert "missing: strict-transport-security" in out
    for private in ("SUPER-SECRET", "TOKEN-KEY", "a-real-password", "the-boss", "someone", "example.com"):
        assert private not in out, private

    monkeypatch.setattr(settings, "dashboard_password", __import__("config.settings", fromlist=["x"])._DEFAULT_PASSWORD)
    (tmp_path / ".env").write_text("OTHER=1\n")
    monkeypatch.setattr(S, "problems", [])
    S.the_site()
    out = capsys.readouterr().out
    assert "dashboard password            : the DEFAULT" in out and "session signing key           : NOT SET" in out
    assert any("printed in the public source code" in p for p in S.problems)


def test_it_runs_on_a_machine_with_none_of_the_tools_and_guesses_nothing(monkeypatch, capsys):
    _fake(monkeypatch, {})
    assert S.main([]) == 0
    out = capsys.readouterr().out
    assert out.count("could not tell") >= 7
    assert "firewall (ufw)                : could not tell" in out, "unknown was read as off"
    # Unknown is never a finding: systemd that cannot be asked is not "off".
    assert "fail2ban (bans repeat guessers): could not tell" in out
    assert "certificate renews by itself  : could not tell" in out
    assert not any(p.startswith(("fail2ban", "certificate renews", "firewall", "SSH", "Redis", "the site runs"))
                   for p in S.problems), S.problems
    assert "WHAT THIS CANNOT SEE" in out and "DigitalOcean: two-step sign-in" in out


def test_it_only_reads():
    src = _PATH.read_text()
    for writer in (".write_text(", ".write_bytes(", "os.remove", "unlink(", "os.replace(", '"w")', "chmod(", "kill("):
        assert writer not in src, writer
    # Every program it runs is a read: nothing with restart, set, delete, enable or reload.
    cmds = __import__("re").findall(r"run\(\[([^\]]+)\]", src)
    assert cmds and not any(w in c for c in cmds for w in ("restart", "config\", \"set", "--delete", "enable", "reload", "allow", "deny"))
