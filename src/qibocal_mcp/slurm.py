"""An access-node MCP gateway; only SLURM workers connect to instruments."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import CallToolResult, TextContent

from .targets import Targets
from .sessions import SessionSource


class SlurmJobs:
    """Serialize jobs and carry only accepted platform state between them."""

    def __init__(
        self,
        platform: str,
        *,
        partition: str,
        output: Path,
        python: str,
        workdir: Path,
        slurm_args: list[str],
        report_rendering: str = "server",
    ):
        self.platform = platform
        self.session_id = uuid4().hex
        self.output = output.resolve() / "sessions" / self.session_id
        self.command = [
            "srun",
            *slurm_args,
            f"--partition={partition}",
            "--nodes=1",
            "--ntasks=1",
            "--quiet",
            "--kill-on-bad-exit=1",
            "--export=ALL",
            f"--chdir={workdir.resolve()}",
            python,
            "-m", "qibocal_mcp.worker",
            "--platform", platform,
            "--output", str(output.resolve()),
            "--session-id", self.session_id,
        ]
        if report_rendering not in {"server", "client"}:
            raise ValueError("report_rendering must be server or client")
        if report_rendering != "server":
            self.command += ["--report-rendering", report_rendering]
        self.snapshot: Path | None = None
        self.runs: set[str] = set()
        self.lock = asyncio.Lock()

    async def call(self, operation: str, arguments: dict) -> CallToolResult:
        """Await one allocation; cancellation terminates srun and its job step."""
        async with self.lock:
            if (
                operation == "update_platform"
                or (operation == "start_session" and arguments.get("source") == "run")
                or (operation in {"append_note", "get_notes"} and arguments.get("run_id") is not None)
            ) and arguments.get("run_id") not in self.runs:
                raise ValueError("Unknown run_id in this gateway session")
            command = list(self.command)
            if self.snapshot is not None:
                command += ["--snapshot", str(self.snapshot)]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await process.communicate(
                    json.dumps(
                        {"operation": operation, "arguments": arguments}
                    ).encode()
                )
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=10)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
            if stderr:
                sys.stderr.write(stderr.decode())
            if process.returncode != 0:
                raise RuntimeError(
                    f"SLURM worker exited with status {process.returncode}: "
                    f"{stderr.decode()}"
                )
            result = CallToolResult.model_validate_json(stdout)
            if result.isError:
                raise RuntimeError(f"SLURM worker returned an error: {result.content}")
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
            elif operation == "append_note":
                if result.structuredContent is None:
                    raise ValueError("Worker response has no notes history")
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
    report_rendering: str = "server",
) -> FastMCP:
    """Expose the same calibration tools without hardware on the access node."""
    jobs = SlurmJobs(
        platform,
        partition=partition,
        output=output,
        python=python,
        workdir=workdir,
        slurm_args=slurm_args,
        report_rendering=report_rendering,
    )
    mcp = FastMCP(
        "Qibocal SLURM",
        instructions=(
            "Read qibocal://context or call get_calibration_context for protocol "
            "parameters. Every operation "
            "runs in a separate SLURM job. Inspect images before updating. "
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

    @mcp.tool
    async def get_platform_state(path: str = "") -> dict:
        """Inspect current Parameters and Calibration without executing a protocol.

        path is an RFC 6901 JSON pointer; empty returns both complete models.
        Mapping keys are JSON strings. Returns path, value and session metadata.
        """
        result = await jobs.call("get_platform_state", {"path": path})
        if result.structuredContent is None:
            raise ValueError("Worker response has no platform state")
        return result.structuredContent

    @mcp.tool
    async def patch_platform(changes: dict) -> dict:
        """Accept explicit scalar edits to Parameters and Calibration for this session.

        changes maps existing RFC 6901 JSON pointers to replacement scalar values.
        Inspect get_platform_state first; supply all coupled edits together.
        Both models are validated before application. Saves an immutable snapshot
        and updates subsequent executions, without overwriting source files.
        For one execution only, use execute_protocol platform_overrides instead.
        """
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
        result = await jobs.call("start_session", {
            "source": source, "run_id": run_id, "ref": ref,
            "platform_path": platform_path, "snapshot_path": snapshot_path,
            "fetch": fetch, "remote": remote,
        })
        if result.structuredContent is None:
            raise ValueError("Worker response has no session metadata")
        return result.structuredContent

    @mcp.tool
    async def execute_protocol(
        protocol: str, parameters: dict, targets: Targets,
        platform_overrides: dict | None = None,
    ) -> ToolResult:
        """Run one protocol in a new SLURM job, without fitted platform updates.

        Use qibocal://context for protocols, parameters, units and qubit IDs.
        Returns run metadata, fitting tables and report images over MCP.
        platform_overrides maps existing JSON pointers to scalar trial values.
        Trials use isolated validated models and never change accepted session
        state, even on failure. Hardware effects are not rolled back.
        """
        result = await jobs.call(
            "execute_protocol",
            {"protocol": protocol, "parameters": parameters, "targets": targets,
             "platform_overrides": platform_overrides},
        )
        return ToolResult(
            content=result.content, structured_content=result.structuredContent
        )

    @mcp.tool
    async def update_platform(run_id: str) -> ToolResult:
        """Apply an inspected run in a new job and save its platform snapshot.

        The accepted snapshot is loaded by subsequent jobs in this session.
        Source platform files are not overwritten.
        """
        result = await jobs.call("update_platform", {"run_id": run_id})
        return ToolResult(
            content=result.content, structured_content=result.structuredContent
        )

    return mcp
