"""Progress of a running acquisition, from Qibolab's duration estimates.

Qibolab logs an estimate for every platform execution. A protocol may execute
many times, so the total is unknown upfront: progress reports the executions
so far and the sum of their estimates. The hardware process also writes each
update to stderr as a marked JSON line, which the SLURM gateway reads.
"""

import json
import logging
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field

MARKER = "qibocal-mcp-progress "
ESTIMATE = re.compile(r"Pulse execution time: ([0-9.eE+-]+) s")
LOGGER = "qibolab"


@dataclass
class Progress:
    started: float = field(default_factory=time.monotonic)
    executions: int = 0
    estimated_s: float = 0.0
    last_estimate_s: float | None = None

    def summary(self) -> dict:
        return {
            "elapsed_s": round(time.monotonic() - self.started, 1),
            "executions": self.executions,
            "estimated_s": round(self.estimated_s, 1),
            "last_estimate_s": self.last_estimate_s,
        }

    def record(self, values: dict) -> None:
        """Apply an update read from a marked line."""
        for name in ("executions", "estimated_s", "last_estimate_s"):
            if name in values:
                setattr(self, name, values[name])


def parse(line: str) -> dict | None:
    """The update carried by a marked stderr line, if it is one."""
    if line.startswith(MARKER):
        try:
            return json.loads(line[len(MARKER):])
        except ValueError:
            return None
    return None


class Cancelled(Exception):
    """A run stopped on request, between two platform executions."""


class Capture(logging.Handler):
    """Collect Qibolab's estimates during one acquisition.

    The estimate is logged right before each platform execution, which makes it
    the point where a requested cancellation stops the run: the exception
    propagates from the logging call, so no further pulses are played.
    """

    def __init__(self, progress: Progress, cancel: threading.Event | None = None):
        super().__init__(logging.INFO)
        self.progress = progress
        self.cancel = cancel

    def emit(self, record: logging.LogRecord) -> None:
        match = ESTIMATE.search(record.getMessage())
        if match is None:
            return
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("Cancelled before a platform execution")
        estimate = float(match.group(1))
        self.progress.executions += 1
        self.progress.estimated_s += estimate
        self.progress.last_estimate_s = round(estimate, 3)
        announce(self.progress)

    def __enter__(self):
        logger = logging.getLogger(LOGGER)
        self.level = logger.level
        # Estimates are INFO records, below the usual default level.
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)
        logger.addHandler(self)
        announce(self.progress)
        return self

    def __exit__(self, *exc):
        logger = logging.getLogger(LOGGER)
        logger.removeHandler(self)
        logger.setLevel(self.level)


def announce(progress: Progress) -> None:
    values = {name: value for name, value in asdict(progress).items() if name != "started"}
    print(MARKER + json.dumps(values), file=sys.stderr, flush=True)
