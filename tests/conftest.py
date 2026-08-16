"""Shared test isolation.

Some state lives in files resolved at import time from settings, which means a
test that forgets to redirect one writes to the REAL path under clips/. That is
not just untidy: the trial ledger is keyed on Twitch id and never forgets, so
one test recording "tw1" made a completely unrelated test fail on the next run
with 'expired' != 'trialing'. Order-dependent failures like that cost far more
to diagnose than they do to prevent.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_trial_ledger(tmp_path, monkeypatch):
    """Point the trial ledger at a per-test file.

    Autouse on purpose. Any test that creates a Twitch user touches the ledger,
    which is most of the suite by now, and requiring each one to remember is how
    the leak happened in the first place.
    """
    from src.auth import trial_ledger
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
