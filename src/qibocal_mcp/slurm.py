"""An access-node MCP gateway; only SLURM workers connect to instruments."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import CallToolResult, TextContent
from qibocal.auto.task import Targets


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
        self.output = output.resolve() / "sessions" / uuid4().hex
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
            "--output", str(self.output),
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
            if operation == "update_platform" and arguments["run_id"] not in self.runs:
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
            elif operation == "update_platform":
                if result.structuredContent is None:
                    raise ValueError("Worker response has no platform snapshot")
                self.snapshot = Path(result.structuredContent["snapshot"])
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
    """Expose the same three tools without opening hardware on the access node."""
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
            "Run handles last for this gateway session; paths are on the cluster."
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
    async def execute_protocol(
        protocol: str, parameters: dict, targets: Targets
    ) -> ToolResult:
        """Run one protocol in a new SLURM job, without fitted platform updates.

        Use qibocal://context for protocols, parameters, units and qubit IDs.
        Returns run metadata, fitting tables and report images over MCP.
        """
        result = await jobs.call(
            "execute_protocol",
            {"protocol": protocol, "parameters": parameters, "targets": targets},
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
