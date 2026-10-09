"""A single-platform, stdio MCP server with explicit calibration updates."""

import argparse
import asyncio
import inspect
import json
import sys
from contextlib import asynccontextmanager, redirect_stdout
from pathlib import Path
from uuid import uuid4

from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import ContentBlock, TextContent
from qibocal import Executor, create_calibration_platform
from qibocal.auto.operation import Protocol
from qibocal.auto.serialize import serialize
from qibocal.auto.task import Completed, Targets
from qibocal.protocols import PROTOCOLS


def create_server(platform_name: str, *, output: Path = Path("runs")) -> FastMCP:
    """Create one server; execution handles live only for its lifetime."""
    # Qibocal's registry also contains exported parameter/data classes.
    protocols = {
        name: protocol
        for name, protocol in PROTOCOLS.items()
        if isinstance(protocol, Protocol)
    }
    completed_runs: dict[str, Completed] = {}
    updated_runs: set[str] = set()
    lock = asyncio.Lock()
    output = output.resolve()

    @asynccontextmanager
    async def lifespan(server):
        # Hardware drivers and Qibocal may print; stdout belongs to MCP.
        with redirect_stdout(sys.stderr):
            platform = create_calibration_platform(platform_name)
            executor = Executor(platform, update=False)
            platform.connect()
        try:
            yield {"executor": executor}
        finally:
            with redirect_stdout(sys.stderr):
                platform.disconnect()

    mcp = FastMCP(
        "Qibocal",
        instructions=(
            "Read qibocal://context for platform targets and protocol parameters. "
            "execute_protocol acquires, fits and reports without applying fitted "
            "updates. Inspect the returned plots before calling update_platform. "
            "Run handles are valid only in this server process."
        ),
        lifespan=lifespan,
    )

    @mcp.resource("qibocal://context")
    async def context(ctx: Context) -> str:
        """Platform targets and built-in protocol parameter definitions, with units."""
        executor = ctx.lifespan_context["executor"]
        return json.dumps(
            {
                "platform": platform_name,
                "qubits": list(executor.platform.qubits),
                "protocols": {
                    name: {
                        "parameters": inspect.getsource(protocol.parameters_type),
                        "two_qubit_gates": protocol.two_qubit_gates,
                        "supports_update": protocol.update is not None,
                    }
                    for name, protocol in protocols.items()
                },
                "common_parameters": (
                    "nshots (integer) and relaxation_time (ns) may be supplied "
                    "with any protocol. Omit them to use platform defaults."
                ),
            },
            indent=2,
        )

    @mcp.tool
    async def execute_protocol(
        protocol: str,
        parameters: dict,
        targets: Targets,
        ctx: Context,
    ) -> ToolResult:
        """Acquire, fit and report, without applying fitted platform updates.

        Choose a built-in protocol and parameters from qibocal://context.
        targets must be qubit IDs, or lists of IDs for multi-qubit protocols.
        Returns a run_id, fitted results, report tables and PNG report images.
        """
        if protocol not in protocols:
            raise ValueError(f"Unknown protocol: {protocol}")
        if not targets:
            raise ValueError("Supply at least one target")
        # JSON arrays represent Qibocal's tuple-valued multi-qubit targets.
        selected = [tuple(t) if isinstance(t, list) else t for t in targets]
        async with lock:
            executor = ctx.lifespan_context["executor"]
            with redirect_stdout(sys.stderr):
                bound = protocols[protocol](**parameters)
                completed = executor(bound, targets=selected)
                run_id = uuid4().hex
                folder = output / run_id
                folder.mkdir(parents=True)
                completed.data.save(folder)
                if completed.results is not None:
                    completed.results.save(folder)
                metadata = {
                    "run_id": run_id,
                    "protocol": protocol,
                    "parameters": parameters,
                    "targets": selected,
                    "folder": str(folder),
                    "results": (
                        serialize(completed.results.params)
                        if completed.results is not None
                        else None
                    ),
                    "supports_update": protocols[protocol].update is not None,
                    "updated": False,
                }
                (folder / "run.json").write_text(
                    json.dumps(metadata, indent=2), encoding="utf-8"
                )
                content: list[ContentBlock] = [
                    TextContent(type="text", text=json.dumps(metadata))
                ]
                reports = completed.reports
                if reports is not None:
                    # Built-in Qibocal reports return (Plotly figures, HTML table).
                    entries = (
                        reports.items()
                        if isinstance(reports, dict)
                        else [("all", reports)]
                    )
                    for index, (target, (figures, table)) in enumerate(entries):
                        content.append(
                            TextContent(type="text", text=f"Target {target}: {table}")
                        )
                        for number, figure in enumerate(figures):
                            stem = folder / f"report-{index}-{number}"
                            figure.write_html(stem.with_suffix(".html"))
                            figure.write_image(
                                stem.with_suffix(".png"), width=1200, height=700
                            )
                            content.append(
                                Image(path=stem.with_suffix(".png")).to_image_content()
                            )
                completed_runs[run_id] = completed
                return ToolResult(content=content, structured_content=metadata)

    @mcp.tool
    async def update_platform(run_id: str, ctx: Context) -> dict:
        """Apply an inspected run's fitted results using Executor.update().

        Updates the live platform used by subsequent executions and writes a
        platform snapshot under the run folder. Does not overwrite the source
        platform in QIBOLAB_PLATFORMS. A run can be applied only once.
        """
        async with lock:
            if run_id not in completed_runs:
                raise ValueError(f"Unknown run_id in this server session: {run_id}")
            if run_id in updated_runs:
                raise ValueError(f"Run already applied: {run_id}")
            completed = completed_runs[run_id]
            if completed.results is None:
                raise ValueError("Run has no fitted results")
            executor = ctx.lifespan_context["executor"]
            with redirect_stdout(sys.stderr):
                executor.update(completed)
                updated_runs.add(run_id)
                snapshot = output / run_id / "platform"
                snapshot.mkdir()
                executor.platform.dump(snapshot)
                path = output / run_id / "run.json"
                metadata = json.loads(path.read_text(encoding="utf-8"))
                metadata["updated"] = True
                path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            return {"run_id": run_id, "updated": True, "snapshot": str(snapshot)}

    return mcp


def main():
    """Run the server over stdio."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--output", type=Path, default=Path("runs"))
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        server = create_server(args.platform, output=args.output)
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
