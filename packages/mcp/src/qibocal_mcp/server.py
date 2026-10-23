"""A responsive MCP frontend with background hardware initialization."""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from pathlib import Path
from uuid import UUID, uuid4

import click
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.tools import ToolResult
from mcp.types import TextResourceContents

from .calibration import register_calibration_prompt
from .plots import APP, register_plot_view
from .sessions import SessionSource
from .targets import Overrides, Targets

logger = logging.getLogger(__name__)


def create_server(
    platform_name: str,
    *,
    output: Path = Path("runs"),
    snapshot: Path | None = None,
    restore_runs: bool = False,
    session_id: str | None = None,
) -> FastMCP:
    """Advertise tools immediately and own one main-thread hardware subprocess."""
    session_id = uuid4().hex if session_id is None else UUID(session_id).hex
    arguments = [
        "-m", "qibocal_mcp.runtime",
        "--platform", platform_name,
        "--output", str(output.resolve()),
        "--session-id", session_id,
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
            "get_platform_state inspects parameters and calibration; "
            "patch_platform accepts explicit session edits. Use platform_overrides "
            "on execute_protocol for isolated trials. "
            "start_session selects a new baseline and report folder. "
            "Use append_note to record session plans and inspected per-run findings; "
            "get_notes reads their append-only histories. "
            "Run handles expire on a session switch or server exit."
        ),
        lifespan=lifespan,
    )
    register_calibration_prompt(mcp)
    register_plot_view(mcp)

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
    async def get_platform_state(ctx: Context, path: str = "") -> dict:
        """Inspect current Parameters and Calibration without executing a protocol.

        path is an RFC 6901 JSON pointer; empty returns both complete models.
        Mapping keys are JSON strings. Returns path, value and session metadata.
        """
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("get_platform_state", {"path": path})
        if result.structured_content is None:
            raise ValueError("Hardware process returned no platform state")
        return result.structured_content

    @mcp.tool
    async def patch_platform(changes: dict, ctx: Context) -> dict:
        """Accept explicit scalar edits to Parameters and Calibration for this session.

        changes maps existing RFC 6901 JSON pointers to replacement scalar values.
        Inspect get_platform_state first; supply all coupled edits together.
        Both models are validated before application. Saves an immutable snapshot
        and updates subsequent executions, without overwriting source files.
        For one execution only, use execute_protocol platform_overrides instead.
        """
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("patch_platform", {"changes": changes})
        if result.structured_content is None:
            raise ValueError("Hardware process returned no edit metadata")
        return result.structured_content

    @mcp.tool
    async def get_notes(ctx: Context, run_id: str | None = None) -> dict:
        """Read session comments, or one execution's comments using its run_id.

        Returns session_id, report_folder, run_id and notes in append order.
        Each note has content, a UTC timestamp and an optional author.
        Reading session notes alone creates no report folder.
        Old run handles expire on a session switch or server exit.
        """
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("get_notes", {"run_id": run_id})
        if result.structured_content is None:
            raise ValueError("Hardware process returned no notes history")
        return result.structured_content

    @mcp.tool
    async def append_note(
        content: str, ctx: Context, run_id: str | None = None,
        author: str | None = None,
    ) -> dict:
        """Append a session comment, or a comment on one execution via run_id.

        content must be nonblank. The server generates a UTC timestamp.
        author is an optional caller-supplied label, not authenticated identity.
        Earlier comments are retained; append follow-up findings rather than edit.
        Returns session_id, report_folder, run_id and the complete notes history.
        Creates the report if needed, without changing calibration state.
        Old run handles expire on a session switch or server exit.
        """
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("append_note", {
            "content": content, "run_id": run_id, "author": author,
        })
        if result.structured_content is None:
            raise ValueError("Hardware process returned no notes history")
        return result.structured_content

    @mcp.tool
    async def start_session(
        ctx: Context,
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
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("start_session", {
            "source": source, "run_id": run_id, "ref": ref,
            "platform_path": platform_path, "snapshot_path": snapshot_path,
            "fetch": fetch, "remote": remote,
        })
        if result.structured_content is None:
            raise ValueError("Hardware process returned no session metadata")
        return result.structured_content

    @mcp.tool(app=APP)
    async def execute_protocol(
        protocol: str, parameters: dict, targets: Targets, ctx: Context,
        platform_overrides: Overrides = None,
    ) -> ToolResult:
        """Acquire, fit and report, without applying fitted platform updates.

        Choose a built-in protocol and parameters from qibocal://context.
        targets must be qubit IDs, or lists of IDs for multi-qubit protocols.
        Returns a run_id, fitted results, report tables and PNG report images.
        platform_overrides maps existing JSON pointers to scalar trial values.
        Trials use isolated validated models and never change accepted session
        state, even on failure. Hardware effects are not rolled back.
        After each call, before another tool call, show the user the returned
        images, unless the interface already displays them, and your fit reading.
        """
        client = await asyncio.shield(ctx.lifespan_context["ready"])
        result = await client.call_tool("execute_protocol", {
            "protocol": protocol, "parameters": parameters, "targets": targets,
            "platform_overrides": platform_overrides,
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


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog="""\b
Examples:
  qibocal-mcp --platform qw5q --output runs
  qibocal-mcp --platform qw5q --partition qpu --output /shared/runs
""",
)
@click.version_option(package_name="qibocal-mcp", prog_name="qibocal-mcp")
@click.option("--platform", required=True, help="Qibolab platform name.")
@click.option(
    "--output", default="runs", show_default=True, help="Report output folder."
)
@click.option(
    "--platforms-dir",
    help="Folder with the platform definitions (sets QIBOLAB_PLATFORMS); "
    "~ is expanded.",
)
@click.option(
    "--partition",
    help="SLURM partition: dispatch every operation as its own job, from a "
    "hardware-free gateway.",
)
@click.option(
    "--worker-python", default=sys.executable, show_default="this interpreter",
    help="Python interpreter of the SLURM jobs.",
)
@click.option(
    "--workdir", default=lambda: str(Path.cwd()), show_default="current folder",
    help="Working directory of the SLURM jobs.",
)
@click.option("--slurm-arg", multiple=True, help="Extra srun option (repeatable).")
def main(
    platform: str, output: str, platforms_dir: str | None, partition: str | None,
    worker_python: str, workdir: str, slurm_arg: tuple[str, ...],
) -> None:
    """Serve Qibocal calibration tools over MCP, on stdio.

    Run it where the instruments are reachable, or on a SLURM access node with
    --partition. Register it as the command of any MCP client; it reads no
    configuration file.
    """
    if slurm_arg and partition is None:
        raise click.UsageError("--slurm-arg requires --partition")
    if platforms_dir is not None:
        # Remote launches pass "~" through unexpanded: resolve it on this host.
        os.environ["QIBOLAB_PLATFORMS"] = os.pathsep.join(
            os.path.expanduser(entry) for entry in platforms_dir.split(os.pathsep)
        )
    with redirect_stdout(sys.stderr):
        if partition is None:
            server = create_server(platform, output=Path(output))
        else:
            from .slurm import create_slurm_server

            server = create_slurm_server(
                platform,
                output=Path(output),
                partition=partition,
                python=worker_python,
                workdir=Path(workdir),
                slurm_args=list(slurm_arg),
            )
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
