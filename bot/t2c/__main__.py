"""``python3 -m bot.t2c`` prints the signal as JSON. Nothing is signed."""
from __future__ import annotations

import json
import sys

from bot.t2c.exit_model import fork_demo
from bot.t2c.pool import fork_rehearsal
from bot.t2c.reading import assemble_reading
from bot.t2c.text import format_signal_text


def main() -> int:
    reading = assemble_reading()
    reading["admin"]["fork_model"] = fork_demo()
    reading["admin"]["pool_rehearsal"] = fork_rehearsal()
    reading["text"] = format_signal_text(reading)
    sys.stdout.write(json.dumps(reading))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
