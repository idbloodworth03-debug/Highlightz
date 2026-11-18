"""The whole-ledger scans, in a process of their own.

    python -m src.stats.ledger_scan <path to stream_stats.jsonl>

Started by stream_stats._scan_in_child, never by hand. It runs the same two
functions the admin page has always used (all_rows and totals_by_user), on
the file it is given, and prints one line of JSON after a marker.

WHY A PROCESS (prod, 2026-11-18, 59 channels live). The two scans parse every
line the ledger has ever been given: about four seconds of pure Python
between them at 400,000 rows. They had been moved to a worker THREAD so the
app's one loop would keep turning, and it did not help: Python runs one
thread at a time, so while a thread is busy the loop has to wait to get the
interpreter back after every socket read and every accepted connection. The
timing report showed it exactly: 13 of the loop's 57 busy points were
waiting, piled on the rows that read sockets, one `accept` step 1.2 seconds
long with 75 ms of work in it. A process has its own interpreter and runs on
one of the cores the app cannot otherwise use (146% of 800% in use).

Only this file and stream_stats are imported: no dashboard, no workers.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MARKER = "@@ledger-scan@@"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m src.stats.ledger_scan <ledger file>", file=sys.stderr)
        return 2
    try:
        os.nice(10)                 # behind the feeds: nothing is waiting on this
    except OSError:
        pass
    from src.stats import stream_stats as st
    st._LOG_FILE = Path(argv[0])    # noqa: SLF001  (the file the caller means)
    out = {"rows": st.all_rows(), "totals": st.totals_by_user()}
    # After a marker, on its own line: a library that prints a warning on the
    # way cannot be mistaken for the answer.
    sys.stdout.write("\n" + MARKER + json.dumps(out) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
