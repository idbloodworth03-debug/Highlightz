"""Send a test alert email: `venv/bin/python -m src.alerts` on the server.

Prints where it went, or exactly why it did not, and exits non-zero on failure
so a setup step can tell.
"""

import asyncio
import socket

from config.settings import settings
from src.alerts import send


def main() -> int:
    ok, why = asyncio.run(send(
        "Highlightz: test alert",
        "This is a test from the Highlightz server "
        f"({socket.gethostname()}).\n\nIf you are reading it, alert emails work: "
        "you will get one like it when something breaks."))
    print(f"sent to {settings.alert_email_to}" if ok else f"NOT sent: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
