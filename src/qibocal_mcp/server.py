"""A single-platform, stdio MCP server with explicit calibration updates."""

import argparse
import asyncio
import inspect
import json
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from pathlib import Path
from uuid import UUID, uuid4

from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import ContentBlock, EmbeddedResource, TextContent, TextResourceContents
from qibocal import Executor, create_calibration_platform
from qibocal.auto.operation import Protocol
from qibocal.auto.serialize import serialize
from qibocal.auto.task import Completed, Targets
from qibocal.calibration.calibration import Calibration
from qibocal.protocols import PROTOCOLS

from .config import add_deployment_arguments, parse_settings
from .calibration import REPORT_FIGURE_MIME_TYPE


def create_server(
    platform_name: str,
    *,
    output: Path = Path("runs"),
    snapshot: Path | None = None,
    restore_runs: bool = False,
    report_rendering: str = "server",
) -> FastMCP:
    """Create one server; execution handles live only for its lifetime."""
    if report_rendering not in {"server", "client"}:
        raise ValueError("report_rendering must be server or client")
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
            if snapshot is not None:
                platform.parameters = type(platform.parameters).model_validate_json(
                    (snapshot / "parameters.json").read_text(encoding="utf-8")
                )
                platform.calibration = Calibration.model_validate_json(
                    (snapshot / "calibration.json").read_text(encoding="utf-8")
                )
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
            "Read qibocal://context or call get_calibration_context for platform "
            "targets and protocol parameters. "
            "execute_protocol acquires, fits and reports without applying fitted "
            "updates. Inspect the returned plots before calling update_platform. "
            "Run handles are valid only in this server process."
        ),
        lifespan=lifespan,
    )

    async def calibration_context(ctx: Context) -> str:
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
                    "platform": platform_name,
                    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
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
                    "update_started": False,
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
                            if report_rendering == "client":
                                serialized = figure.to_json()
                                stem.with_suffix(".json").write_text(
                                    serialized, encoding="utf-8"
                                )
                                content.append(EmbeddedResource(
                                    type="resource",
                                    resource=TextResourceContents(
                                        uri=f"qibocal://reports/{run_id}/{index}/{number}",
                                        mimeType=REPORT_FIGURE_MIME_TYPE,
                                        text=serialized,
                                    ),
                                ))
                            else:
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
        platform in QIBOLAB_PLATFORMS. A run's update can be attempted only once.
        """
        async with lock:
            if run_id not in completed_runs and restore_runs:
                if UUID(run_id).hex != run_id:
                    raise ValueError("Invalid run_id")
                folder = output / run_id
                metadata = json.loads(
                    (folder / "run.json").read_text(encoding="utf-8")
                )
                if metadata["platform"] != platform_name:
                    raise ValueError("Run belongs to a different platform")
                bound = protocols[metadata["protocol"]](**metadata["parameters"])
                completed_runs[run_id] = Completed(
                    path=folder, bound=bound, _targets=metadata["targets"]
                )
                if metadata["update_started"]:
                    updated_runs.add(run_id)
            if run_id not in completed_runs:
                raise ValueError(f"Unknown run_id in this server session: {run_id}")
            if run_id in updated_runs:
                raise ValueError(f"Run already applied or update attempted: {run_id}")
            completed = completed_runs[run_id]
            if completed.results is None:
                raise ValueError("Run has no fitted results")
            if completed.bound is None or completed.bound.protocol.update is None:
                raise ValueError("Protocol does not support updating")
            executor = ctx.lifespan_context["executor"]
            with redirect_stdout(sys.stderr):
                path = output / run_id / "run.json"
                metadata = json.loads(path.read_text(encoding="utf-8"))
                metadata["update_started"] = True
                path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                updated_runs.add(run_id)
                executor.update(completed)
                snapshot = output / run_id / "platform"
                snapshot.mkdir()
                executor.platform.dump(snapshot)
                metadata["updated"] = True
                path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            return {"run_id": run_id, "updated": True, "snapshot": str(snapshot)}

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
