"""Acquisitions as background tasks, so that no request outlives the client.

execute_protocol starts a run and waits for it only up to a bound: clients
time requests out on their own, and the result of a timed-out request is
lost. A run outlasting the wait is reported with its handle and progress, and
get_run waits again. Requests only wait: cancelling one never stops a run;
only cancel_run does.
"""

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastmcp import Context
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from .progress import Cancelled, Progress

# Below the request timeouts common among clients (often 60 s).
WAIT = 45.0
# How often a waiting request reports progress, when the client asks for it.
PULSE = 5.0


@dataclass
class Run:
    run_id: str
    protocol: str
    targets: list
    task: asyncio.Task
    progress: Progress = field(default_factory=Progress)
    # A SLURM job waits for its allocation before its first progress line.
    queued: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)

    def status(self) -> dict:
        if self.cancel.is_set():
            return {
                "run_id": self.run_id,
                "status": "cancelling",
                "protocol": self.protocol,
                "targets": self.targets,
                **self.progress.summary(),
                "message": (
                    "The run stops before its next platform execution: follow it "
                    "with get_run. The current execution is not interrupted."
                ),
            }
        return {
            "run_id": self.run_id,
            "status": "queued" if self.queued else "running",
            "protocol": self.protocol,
            "targets": self.targets,
            **self.progress.summary(),
            "message": (
                "The run continues in the background: wait for its result with "
                "get_run. Do not submit it again."
            ),
        }


class Runs:
    """The runs of one session; at most one acquires at a time.

    With interrupt, cancelling a run also cancels its task: for SLURM jobs,
    whose worker is then terminated. Otherwise the acquisition stops itself
    before its next platform execution, and the task ends with it.
    """

    def __init__(self, interrupt: bool = False):
        self.runs: dict[str, Run] = {}
        self.interrupt = interrupt

    def active(self) -> Run | None:
        return next((run for run in self.runs.values() if not run.task.done()), None)

    def ensure_idle(self) -> None:
        run = self.active()
        if run is not None:
            raise ValueError(
                f"Run {run.run_id} ({run.protocol}) is in progress: wait for it with "
                "get_run first. Notes and reads remain available meanwhile."
            )

    def start(
        self, run_id: str, protocol: str, targets: list,
        work: Callable[[Run], Awaitable[ToolResult]], *, queued: bool = False,
    ) -> Run:
        self.ensure_idle()
        run = Run(run_id, protocol, targets, None, queued=queued)
        run.task = asyncio.create_task(work(run), name=f"run-{run_id}")
        # Failures are reported through get_run; this only marks them retrieved.
        run.task.add_done_callback(lambda task: task.cancelled() or task.exception())
        self.runs[run_id] = run
        return run

    def cancel(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run is None:
            raise ValueError(f"Unknown run_id in this server session: {run_id}")
        if run.task.done():
            raise ValueError(f"Run {run_id} has already finished")
        run.cancel.set()
        if self.interrupt:
            run.task.cancel()

    def clear(self) -> None:
        self.runs.clear()

    async def wait(self, run_id: str, wait: float, ctx: Context | None) -> ToolResult:
        """The run's result, once complete within wait seconds, or its status."""
        run = self.runs.get(run_id)
        if run is None:
            raise ValueError(f"Unknown run_id in this server session: {run_id}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(wait, 0)
        while not run.task.done() and (remaining := deadline - loop.time()) > 0:
            await asyncio.wait({run.task}, timeout=min(remaining, PULSE))
            if not run.task.done() and ctx is not None:
                summary = run.progress.summary()
                await ctx.report_progress(
                    progress=summary["elapsed_s"],
                    message=(
                        f"{run.protocol}: {summary['executions']} executions, "
                        f"~{summary['estimated_s']} s estimated so far"
                    ),
                )
        if run.task.done() and (
            run.task.cancelled() or isinstance(run.task.exception(), Cancelled)
        ):
            status = {
                "run_id": run_id, "status": "cancelled", "protocol": run.protocol,
                "targets": run.targets, **run.progress.summary(),
                "message": (
                    "Cancelled on request: no results were saved. Hardware effects "
                    "of the executions already played are not rolled back."
                ),
            }
        elif run.task.done():
            return run.task.result()
        else:
            status = run.status()
        return ToolResult(
            content=[TextContent(type="text", text=json.dumps(status))],
            structured_content=status,
        )
