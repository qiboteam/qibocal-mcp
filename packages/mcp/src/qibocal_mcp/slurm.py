"""An access-node MCP gateway; only SLURM workers connect to instruments."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from mcp.types import CallToolResult, TextContent

from .calibration import (
    PROTOCOL_DESCRIPTION,
    PROTOCOL_TOOL,
    register_calibration_prompt,
)
from .plots import APP, APP_ONLY, FIGURE_TOOL, register_plot_view
from .progress import parse
from .runs import WAIT, Run, Runs
from .sessions import STATE_DESCRIPTIONS, SessionSource
from .targets import Overrides, Targets
from .versions import DESCRIPTIONS

# squeue fields, in order; the job name identifies our job among the others.
QUEUE_FIELDS = ("job_id", "state", "user", "name", "elapsed", "time_limit", "reason", "priority")
# Running jobs are listed individually only up to this many.
LISTED = 5


def job_name(run_id: str) -> str:
    return f"qibocal-mcp-{run_id}"


def queue_summary(text: str, name: str) -> dict:
    """Our job's state, and how busy its partition is, from squeue output."""
    jobs = [
        dict(zip(QUEUE_FIELDS, map(str.strip, fields), strict=True))
        for line in text.splitlines()
        if len(fields := line.strip().split("|")) == len(QUEUE_FIELDS)
    ]
    ours = next((job for job in jobs if job["name"] == name), None)
    running = [job for job in jobs if job["state"] == "RUNNING"]
    pending = [job for job in jobs if job["state"] == "PENDING" and job is not ours]
    summary = {
        "job": None if ours is None else {
            key: ours[key] for key in ("job_id", "state", "reason")
        },
        "running": len(running),
        "pending": len(pending),
    }

    def rank(job):
        # Higher priority first, then submission order.
        try:
            return (-int(job["priority"]), int(job["job_id"].split("_")[0]))
        except ValueError:
            return (0, 0)

    if ours is not None and ours["state"] == "PENDING":
        summary["pending_ahead"] = sum(rank(job) < rank(ours) for job in pending)
    if len(running) <= LISTED:
        summary["running_jobs"] = [
            {key: job[key] for key in ("job_id", "user", "name", "elapsed", "time_limit")}
            for job in running
        ]
    return summary


class SlurmJobs:
    """Run each operation in a fresh worker, carrying only accepted state.

    Acquisitions are SLURM jobs. Every other operation is software only, and
    runs in a local worker on this node, so it never waits for an allocation.
    """

    def __init__(
        self,
        platform: str,
        *,
        partition: str,
        output: Path,
        python: str,
        workdir: Path,
        slurm_args: list[str],
    ):
        self.platform = platform
        self.session_id = uuid4().hex
        self.output = output.resolve() / "sessions" / self.session_id
        self.workdir = workdir.resolve()
        self.partition = partition
        self.srun = [
            "srun",
            *slurm_args,
            f"--partition={partition}",
            "--nodes=1",
            "--ntasks=1",
            "--quiet",
            "--kill-on-bad-exit=1",
            "--export=ALL",
            f"--chdir={self.workdir}",
        ]
        self.command = [
            python,
            "-m", "qibocal_mcp.worker",
            "--platform", platform,
            "--output", str(output.resolve()),
            "--session-id", self.session_id,
        ]
        self.snapshot: Path | None = None
        self.runs: set[str] = set()
        self.lock = asyncio.Lock()

    async def call(
        self, operation: str, arguments: dict, run: Run | None = None,
    ) -> CallToolResult:
        """Run one operation; with run, an acquisition, as a SLURM job.

        Cancellation terminates the worker, and with it srun's job step.
        Acquisitions release the lock while they run, so that other
        operations proceed meanwhile.
        """
        request = json.dumps({"operation": operation, "arguments": arguments}).encode()
        async with self.lock:
            if (
                operation == "update_platform"
                or (operation == "start_session" and arguments.get("source") == "run")
                or (operation in {"append_note", "get_notes"} and arguments.get("run_id") is not None)
                or operation in {"get_run_results", FIGURE_TOOL}
            ) and arguments.get("run_id") not in self.runs:
                raise ValueError("Unknown run_id in this gateway session")
            command = list(self.command)
            if self.snapshot is not None:
                command += ["--snapshot", str(self.snapshot)]
            if run is None:
                return self.finish(operation, await self.spawn(command, request, None))
            # Named after the run, so that squeue finds it; after the extra
            # SLURM arguments, so that a job name there does not hide it.
            command = [
                *self.srun, f"--job-name={job_name(run.run_id)}",
                *command, "--run-id", run.run_id,
            ]
        stdout = await self.spawn(command, request, run)
        async with self.lock:
            return self.finish(operation, stdout)

    async def spawn(self, command: list[str], request: bytes, run: Run | None) -> bytes:
        """Relay the worker's logs, reading its progress lines, and return stdout."""
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir,
        )

        async def relay() -> str:
            logs = []
            async for line in process.stderr:
                text = line.decode(errors="replace")
                values = parse(text)
                if values is None:
                    logs.append(text)
                    sys.stderr.write(text)
                elif run is not None:
                    # The first line comes from the worker: the job has started.
                    run.queued = False
                    run.progress.record(values)
            return "".join(logs)

        try:
            process.stdin.write(request)
            await process.stdin.drain()
            process.stdin.close()
            stdout, stderr = await asyncio.gather(process.stdout.read(), relay())
            await process.wait()
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        if process.returncode != 0:
            raise RuntimeError(
                f"Worker exited with status {process.returncode}: {stderr}"
            )
        return stdout

    async def queue(self, run_id: str) -> dict:
        """The partition's queue while a run waits; best effort, never fatal."""
        try:
            process = await asyncio.create_subprocess_exec(
                "squeue", "--noheader", f"--partition={self.partition}",
                "--format=" + "|".join(
                    ("%i", "%T", "%u", "%j", "%M", "%l", "%r", "%Q")
                ),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except Exception as error:
            return {"error": f"Could not read the queue: {error}"}
        if process.returncode != 0:
            return {"error": stderr.decode(errors="replace").strip()
                    or f"squeue exited with status {process.returncode}"}
        return queue_summary(stdout.decode(errors="replace"), job_name(run_id))

    def finish(self, operation: str, stdout: bytes) -> CallToolResult:
        """Validate a worker response and carry the accepted state forward."""
        result = CallToolResult.model_validate_json(stdout)
        if result.isError:
            raise RuntimeError(f"Worker returned an error: {result.content}")
        if operation == "execute_protocol":
            if result.structuredContent is None:
                raise ValueError("Worker response has no run metadata")
            self.runs.add(result.structuredContent["run_id"])
            if self.snapshot is None:
                # Pin the baseline instead of re-reading changing source files.
                self.snapshot = Path(result.structuredContent["report_folder"]) / "platform"
        elif operation in {"update_platform", "patch_platform"}:
            if result.structuredContent is None:
                raise ValueError("Worker response has no platform snapshot")
            self.snapshot = Path(result.structuredContent["snapshot"])
        elif operation in {"append_note", "save_platform"}:
            # Both create the report, pinning its baseline for later workers.
            if result.structuredContent is None:
                raise ValueError(f"Worker response has no {operation} result")
            if self.snapshot is None:
                self.snapshot = Path(result.structuredContent["report_folder"]) / "platform"
        elif operation == "start_session":
            if result.structuredContent is None:
                raise ValueError("Worker response has no session metadata")
            metadata = result.structuredContent
            self.session_id = metadata["session_id"]
            self.output = Path(metadata["report_folder"])
            self.snapshot = Path(metadata["snapshot"])
            self.command[self.command.index("--session-id") + 1] = self.session_id
            self.runs.clear()
        return result


def create_slurm_server(
    platform: str,
    *,
    partition: str,
    output: Path,
    python: str,
    workdir: Path,
    slurm_args: list[str],
) -> FastMCP:
    """Expose the same calibration tools without hardware on the access node."""
    jobs = SlurmJobs(
        platform,
        partition=partition,
        output=output,
        python=python,
        workdir=workdir,
        slurm_args=slurm_args,
    )
    runs = Runs(interrupt=True)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            # Stop a job still running when the gateway exits.
            active = runs.active()
            if active is not None:
                active.task.cancel()
                await asyncio.wait({active.task})

    mcp = FastMCP(
        "Qibocal SLURM",
        lifespan=lifespan,
        instructions=(
            "Read qibocal://context or call get_calibration_context for protocol "
            "parameters. Each acquisition runs in a separate SLURM job; other "
            "operations need no allocation. Inspect images before updating. "
            "get_platform_state inspects parameters and calibration; "
            "patch_platform accepts explicit session edits. Use platform_overrides "
            "on execute_protocol for isolated trials. "
            "start_session selects a new baseline and report folder. "
            "Use append_note to record session plans and inspected per-run findings; "
            "get_notes reads their append-only histories. "
            "Run handles expire on a session switch or gateway exit; "
            "paths are on the cluster."
        ),
    )
    register_calibration_prompt(mcp)
    register_plot_view(mcp)

    async def calibration_context() -> str:
        """Discover the platform and protocols through a compute-node job."""
        result = await jobs.call("context", {})
        if len(result.content) != 1 or not isinstance(result.content[0], TextContent):
            raise ValueError("Worker returned an invalid context resource")
        return result.content[0].text

    @mcp.resource("qibocal://context")
    async def context() -> str:
        """Discover the platform and protocols through a compute-node job."""
        return await calibration_context()

    @mcp.tool
    async def get_calibration_context() -> str:
        """Return qibocal://context JSON text through a compute-node discovery job."""
        return await calibration_context()

    async def state(name: str, arguments: dict) -> dict:
        result = await jobs.call(name, arguments)
        if result.structuredContent is None:
            raise ValueError(f"Worker response has no {name} result")
        return result.structuredContent

    @mcp.tool(name=PROTOCOL_TOOL, description=PROTOCOL_DESCRIPTION)
    async def get_protocol(name: str) -> dict:
        return await state(PROTOCOL_TOOL, {"name": name})

    @mcp.tool(description=STATE_DESCRIPTIONS["get_platform_state"])
    async def get_platform_state(path: str = "", depth: int | None = None) -> dict:
        return await state("get_platform_state", {"path": path, "depth": depth})

    @mcp.tool(description=STATE_DESCRIPTIONS["search_platform_state"])
    async def search_platform_state(query: str, path: str = "") -> dict:
        return await state("search_platform_state", {"query": query, "path": path})

    @mcp.tool
    async def patch_platform(changes: dict) -> dict:
        """Accept explicit scalar edits to Parameters and Calibration for this session.

        changes maps existing RFC 6901 JSON pointers to replacement scalar values.
        Inspect get_platform_state first; supply all coupled edits together.
        Both models are validated before application. Saves an immutable snapshot
        and updates subsequent executions, without overwriting source files.
        For one execution only, use execute_protocol platform_overrides instead.
        """
        runs.ensure_idle()
        result = await jobs.call("patch_platform", {"changes": changes})
        if result.structuredContent is None:
            raise ValueError("Worker response has no edit metadata")
        return result.structuredContent

    @mcp.tool
    async def get_notes(run_id: str | None = None) -> dict:
        """Read session comments, or one execution's comments using its run_id.

        Returns session_id, report_folder, run_id and notes in append order.
        Each note has content, a UTC timestamp and an optional author.
        Reading session notes alone creates no report folder.
        Old run handles expire on a session switch or gateway exit.
        """
        result = await jobs.call("get_notes", {"run_id": run_id})
        if result.structuredContent is None:
            raise ValueError("Worker response has no notes history")
        return result.structuredContent

    @mcp.tool
    async def append_note(
        content: str, run_id: str | None = None, author: str | None = None,
    ) -> dict:
        """Append a session comment, or a comment on one execution via run_id.

        content must be nonblank. The worker generates a UTC timestamp.
        author is an optional caller-supplied label, not authenticated identity.
        Earlier comments are retained; append follow-up findings rather than edit.
        Returns session_id, report_folder, run_id and the complete notes history.
        Creates the report if needed, without changing calibration state.
        Old run handles expire on a session switch or gateway exit.
        """
        result = await jobs.call("append_note", {
            "content": content, "run_id": run_id, "author": author,
        })
        if result.structuredContent is None:
            raise ValueError("Worker response has no notes history")
        return result.structuredContent

    @mcp.tool
    async def start_session(
        source: SessionSource = "current",
        run_id: str | None = None,
        ref: str | None = None,
        platform_path: str | None = None,
        snapshot_path: str | None = None,
        fetch: bool = False,
        remote: str | None = None,
    ) -> dict:
        """Switch to a fresh calibration session and report folder.

        current restores the latest accepted update, or the session baseline.
        initial restores this session's baseline. run applies an inspected run's
        fitted results to its execution checkpoint, not to later updates.
        repository reads committed parameters/calibration at ref (default HEAD);
        platform_path is the execution-host platform folder (defaults to lookup).
        fetch=true first fetches ref from a configured remote (default origin);
        HEAD then means the remote's HEAD. No checkout, pull or driver reload.
        remote requires fetch=true; both require source=repository.
        snapshot loads paired files from snapshot_path on the execution host.
        Old run handles expire; source files and old reports are not modified.
        Inspect and approve fitted results before selecting source=run.
        """
        runs.ensure_idle()
        result = await jobs.call("start_session", {
            "source": source, "run_id": run_id, "ref": ref,
            "platform_path": platform_path, "snapshot_path": snapshot_path,
            "fetch": fetch, "remote": remote,
        })
        if result.structuredContent is None:
            raise ValueError("Worker response has no session metadata")
        runs.clear()
        return result.structuredContent

    async def local(operation: str, arguments: dict) -> dict:
        result = await jobs.call(operation, arguments)
        if result.structuredContent is None:
            raise ValueError(f"Worker response has no {operation} result")
        return result.structuredContent

    @mcp.tool(description=DESCRIPTIONS["list_versions"])
    async def list_versions(
        ref: str | None = None, limit: int = 20, fetch: bool = False,
        remote: str = "origin",
    ) -> dict:
        return await local("list_versions", {
            "ref": ref, "limit": limit, "fetch": fetch, "remote": remote,
        })

    @mcp.tool(description=DESCRIPTIONS["diff_platform"])
    async def diff_platform(against: str = "source") -> dict:
        return await local("diff_platform", {"against": against})

    @mcp.tool(description=DESCRIPTIONS["save_platform"])
    async def save_platform(
        message: str, branch: str, confirm: str | None = None,
        push: bool = False, remote: str | None = None,
    ) -> dict:
        return await local("save_platform", {
            "message": message, "branch": branch, "confirm": confirm,
            "push": push, "remote": remote,
        })

    @mcp.tool(description=DESCRIPTIONS["checkout_platform"])
    async def checkout_platform(ref: str) -> dict:
        return await local("checkout_platform", {"ref": ref})

    @mcp.tool(app=APP)
    async def execute_protocol(
        protocol: str, parameters: dict, targets: Targets, ctx: Context,
        platform_overrides: Overrides = None, wait: float = WAIT,
    ) -> ToolResult:
        """Run one protocol in a new SLURM job, without fitted platform updates.

        Use qibocal://context for protocols, parameters, units and qubit IDs.
        Returns run metadata, fitting tables and report images over MCP.
        platform_overrides maps existing JSON pointers to scalar trial values.
        Trials use isolated validated models and never change accepted session
        state, even on failure. Hardware effects are not rolled back.
        Waits up to wait seconds, queue included: a longer run returns its
        run_id and progress and continues; get_run waits for its result.
        After each result with plots, before another tool call, message the user
        your fit reading, showing the plots as the result's opening note says.
        """

        async def work(run: Run) -> ToolResult:
            result = await jobs.call("execute_protocol", {
                "protocol": protocol, "parameters": parameters, "targets": targets,
                "platform_overrides": platform_overrides,
            }, run)
            return ToolResult(
                content=result.content, structured_content=result.structuredContent,
                meta=result.meta,
            )

        run_id = uuid4().hex
        run = runs.start(run_id, protocol, targets, work, queued=True)
        run.queue = lambda: jobs.queue(run_id)
        return await runs.wait(run_id, wait, ctx)

    @mcp.tool(app=APP)
    async def get_run(run_id: str, ctx: Context, wait: float = WAIT) -> ToolResult:
        """Wait for a run started by execute_protocol, and return its result.

        Waits up to wait seconds. A completed run returns what execute_protocol
        returns: metadata, report tables and images. A run still in progress
        returns its status and progress again; a failed run returns its error.
        """
        return await runs.wait(run_id, wait, ctx)

    @mcp.tool
    async def cancel_run(run_id: str, ctx: Context, wait: float = WAIT) -> ToolResult:
        """Stop a run started by execute_protocol, then wait for it to stop.

        Use it only when the user asks, or when a run exceeds approved limits or
        budget. A local run stops before its next platform execution; a SLURM
        job is terminated. Waits up to wait seconds; get_run follows it further.
        A cancelled run saves no results, and hardware effects are not undone.
        """
        runs.cancel(run_id)
        return await runs.wait(run_id, wait, ctx)

    @mcp.tool
    async def get_run_results(run_id: str, fields: list[str] | None = None) -> dict:
        """Return a completed run's fitted results in full, on request only.

        Results normally omit large values, such as per-shot arrays. Without
        fields, lists the available fields with their sizes in bytes; with
        fields, returns those values. Request only what a decision needs.
        """
        result = await jobs.call("get_run_results", {"run_id": run_id, "fields": fields})
        if result.structuredContent is None:
            raise ValueError("Worker response has no results")
        return result.structuredContent

    @mcp.tool(name=FIGURE_TOOL, app=APP_ONLY)
    async def get_run_figures(run_id: str) -> dict:
        """Interactive figures of a run, for the plot view; not for the model."""
        result = await jobs.call(FIGURE_TOOL, {"run_id": run_id})
        if result.structuredContent is None:
            raise ValueError("Worker response has no figures")
        return result.structuredContent

    @mcp.tool
    async def update_platform(run_id: str) -> ToolResult:
        """Apply an inspected run and save its platform snapshot.

        The accepted snapshot is loaded by subsequent jobs in this session.
        Source platform files are not overwritten.
        """
        runs.ensure_idle()
        result = await jobs.call("update_platform", {"run_id": run_id})
        return ToolResult(
            content=result.content, structured_content=result.structuredContent,
            meta=result.meta,
        )

    return mcp
