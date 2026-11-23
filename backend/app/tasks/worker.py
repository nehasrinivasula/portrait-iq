"""Entry point of the worker process: `python -m app.tasks.worker <task id>`.

Runs one task, then exits. It lowers its own CPU priority first, so the API process wins whenever both want the
CPU, and it shares nothing with the API but the database file and the environment.
"""
from __future__ import annotations

import logging
import os
import sys

from app.config import settings


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m app.tasks.worker <task id>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        os.nice(int(settings.background_task_nice))
    except (OSError, AttributeError):          # not allowed, or not POSIX: carry on at normal priority
        logging.getLogger(__name__).info("could not lower the worker's priority")
    from app.db import engine
    from app.tasks.runner import execute
    execute(engine, argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
