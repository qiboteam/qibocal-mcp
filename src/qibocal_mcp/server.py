"""A responsive MCP frontend with background hardware initialization."""

import argparse
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from pathlib import Path
from uuid import UUID, uuid4

from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.tools import ToolResult
from mcp.types import TextResourceContents

from .config import add_deployment_arguments, parse_settings
from .targets import Targets

logger = logging.getLogger(__name__)


def create_server(
    platform_name: str,
    *,
    output: Path = Path("runs"),
    snapshot: Path | None = None,
    restore_runs: bool = False,
    report_rendering: str = "server",
    session_id: str | None = None,
) -> FastMCP:
    """Advertise tools immediately and own one main-thread hardware subprocess."""
    if report_rendering not in {"server", "client"}:
        raise ValueError("report_rendering must be server or client")
    session_id = uuid4().hex if session_id is None else UUID(session_id).hex
    arguments = [
        "-m", "qibocal_mcp.runtime",
        "--platform", platform_name,
        "--output", str(output.resolve()),
        "--session-id", session_id,
        "--report-rendering", report_rendering,
    ]
    if snapshot is not None:
        arguments += ["--snapshot", str(snapshot.resolve())]
    if restore_runs:
        arguments.append("--restore-runs")

    @asynccontextmanager
    async def lifespan(server):
        ready = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        client = Client(
            StdioTransport(
                command=sys.executable, args=arguments, keep_alive=False,
                env=dict(os.environ),
            ),
            init_timeout=0,
        )

        async def connect():
            async with client:
                ready.set_result(client)
                await stop.wait()

        def finished(task):
            if task.cancelled():
                ready.cancel()
                return
            error = task.exception()
            if error is not None:
                if not ready.done():
                    ready.set_exception(error)
                    ready.exception()
                logger.error(
                    "Qibocal initialization failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

        initialization = asyncio.create_task(connect(), name="qibocal-initialization")
        initialization.add_done_callback(finished)
        try:
            yield {"ready": ready}
        finally:
            stop.set()
            if not ready.done():
                initialization.cancel()
            await asyncio.wait({initialization})

    mcp = FastMCP(
        "Qibocal",
        instructions=(
            "Read qibocal://context or call get_calibration_context for platform "
            "targets and protocol parameters. "
            "execute_protocol acquires, fits and reports without applying fitted "
            "updates. Inspect the returned plots before calling update_platform. "
            "Run handles are valid only in this server process."
        ),
        lifespan=lifespan,
    )

    async def calibration_context(ctx: Context) -> str:
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        content = await client.read_resource("qibocal://context")
        if len(content) != 1 or not isinstance(content[0], TextResourceContents):
            raise ValueError("Hardware process returned an invalid context resource")
        return content[0].text

    @mcp.resource("qibocal://context")
    async def context(ctx: Context) -> str:
        """Platform targets and built-in protocol parameter definitions, with units."""
        return await calibration_context(ctx)

    @mcp.tool
    async def get_calibration_context(ctx: Context) -> str:
        """Return the same JSON text as qibocal://context for calibration discovery."""
        return await calibration_context(ctx)

    @mcp.tool
    async def execute_protocol(
        protocol: str, parameters: dict, targets: Targets, ctx: Context,
    ) -> ToolResult:
        """Acquire, fit and report, without applying fitted platform updates.

        Choose a built-in protocol and parameters from qibocal://context.
        targets must be qubit IDs, or lists of IDs for multi-qubit protocols.
        Returns a run_id, fitted results, report tables and PNG report images.
        """
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("execute_protocol", {
            "protocol": protocol, "parameters": parameters, "targets": targets,
        })
        return ToolResult(
            content=result.content, structured_content=result.structured_content,
            meta=result.meta, is_error=result.is_error,
        )

    @mcp.tool
    async def update_platform(run_id: str, ctx: Context) -> dict:
        """Apply an inspected run's fitted results using Executor.update().

        Updates the live platform used by subsequent executions and writes a
        snapshot under the run folder and the session's new_platform directory.
        Does not overwrite the source platform in QIBOLAB_PLATFORMS.
        A run's update can be attempted only once.
        """
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("update_platform", {"run_id": run_id})
        if result.structured_content is None:
            raise ValueError("Hardware process returned no update metadata")
        return result.structured_content

    return mcp


def main(argv: list[str] | None = None):
    """Run a direct hardware server or a SLURM gateway over stdio."""
    parser = argparse.ArgumentParser(
        description=__doc__, argument_default=argparse.SUPPRESS
    )
    parser.add_argument("--platform")
    parser.add_argument("--output")
    parser.add_argument("--platforms", help="QIBOLAB_PLATFORMS on the execution host")
    parser.add_argument("--partition", help="Dispatch each operation through srun")
    parser.add_argument("--worker-python")
    parser.add_argument("--workdir")
    parser.add_argument("--slurm-arg", action="append")
    add_deployment_arguments(parser)
    args = parse_settings(parser, "server", argv)
    if args.venv is not None or args.platform_repo or args.dependency:
        from .environment import DeploymentError, launch, source_payload

        command = [
            "--no-config", "--platform", args.platform, "--output", args.output,
        ]
        if args.report_rendering != "server":
            command += ["--report-rendering", args.report_rendering]
        if args.partition is not None:
            command += [
                "--partition", args.partition,
                "--workdir", args.workdir,
            ]
            if args.venv is None:
                command += ["--worker-python", args.worker_python]
        command += [f"--slurm-arg={arg}" for arg in args.slurm_arg]
        try:
            launch(
                {
                    "venv": args.venv,
                    "python": sys.executable,
                    "platform": args.platform,
                    "platforms": args.platforms,
                    "platform_repo": args.platform_repo,
                    "dependency": args.dependency,
                    "worker_python": args.worker_python if args.partition is not None and args.venv is None else None,
                },
                command,
                source_payload() if args.venv is not None else None,
            )
        except (DeploymentError, OSError, ValueError) as error:
            parser.error(str(error))
        return
    if args.platforms is not None:
        os.environ["QIBOLAB_PLATFORMS"] = args.platforms
    with redirect_stdout(sys.stderr):
        rendering = (
            {"report_rendering": args.report_rendering}
            if args.report_rendering != "server" else {}
        )
        if args.partition is None:
            server = create_server(args.platform, output=Path(args.output), **rendering)
        else:
            from .slurm import create_slurm_server

            server = create_slurm_server(
                args.platform,
                output=Path(args.output),
                partition=args.partition,
                python=args.worker_python,
                workdir=Path(args.workdir),
                slurm_args=args.slurm_arg,
                **rendering,
            )
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
