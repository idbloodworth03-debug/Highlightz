"""Dependencies are pinned and CI runs the suite (2026-09-30 audit:
"requirements.txt is fully unpinned; no CI runs the test suite").

An unpinned requirements.txt means a reinstall on the droplet pulls whatever
PyPI has that day, untested. What these defend:
  1. every package in requirements.txt is pinned with ==;
  2. requirements.lock is production's pip freeze, and pins the same version
     for each of them; test-only packages (Pillow) live in requirements-test.txt;
  3. the CI workflow installs the lock and runs both the JSX check and the suite.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pins(path):
    out = {}
    for line in (ROOT / path).read_text().splitlines():
        body = line.split("#")[0].strip()
        if not body:
            continue
        assert "==" in body, f"{path}: not pinned: {body!r}"
        name, ver = body.split("==")
        out[re.split(r"\[", name)[0].strip().lower().replace("_", "-")] = ver.strip()
    return out


def test_every_requirement_is_pinned_and_matches_the_lock():
    top, lock = _pins("requirements.txt"), _pins("requirements.lock")
    assert len(top) >= 20 and len(lock) > len(top)
    for name, ver in top.items():
        assert lock.get(name) == ver, f"{name}: requirements.txt {ver} vs lock {lock.get(name)}"


def test_test_only_packages_are_pinned_and_kept_out_of_the_lock():
    extra = _pins("requirements-test.txt")
    assert "pillow" in extra
    assert not set(extra) & set(_pins("requirements.lock")), "a test-only package is in prod's lock"


def test_ci_installs_the_lock_and_runs_the_checks():
    """On the Python production runs (3.12, per the droplet), with its packages."""
    wf = (ROOT / ".github/workflows/tests.yml").read_text()
    assert "pip install -r requirements.lock -r requirements-test.txt" in wf
    assert 'python-version: "3.12"' in wf
    assert "node scripts/check_jsx.js" in wf
    assert "python -m pytest -q" in wf
    assert "push:" in wf and "pull_request:" in wf
