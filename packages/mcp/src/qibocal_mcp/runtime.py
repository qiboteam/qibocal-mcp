"""Internal hardware process; platform drivers run on its main thread."""

import argparse
import asyncio
import inspect
import json
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from copy import copy
from pathlib import Path
from uuid import UUID, uuid4

from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import ContentBlock, TextContent

with redirect_stdout(sys.stderr):
    from qibocal import Executor, Note, create_calibration_platform
    from qibocal.auto.operation import Protocol
    from qibocal.auto.serialize import serialize
    from qibocal.auto.task import Completed
    from qibocal.protocols import PROTOCOLS

    from .artifacts import SessionReport
    from .notes import append_note as persist_note
    from .notes import read_notes
from .calibration import register_calibration_prompt, show_images
from .plots import APP, figures_meta, register_plot_view
from .sessions import (
    PlatformState,
    SessionSource,
    pointer_tokens,
    pointer_value,
    repository_state,
)
from .targets import Overrides, Targets


def create_server(
    platform_name: str,
    *,
    output: Path = Path("runs"),
    snapshot: Path | None = None,
    restore_runs: bool = False,
    session_id: str | None = None,
) -> FastMCP:
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
    session_id = uuid4().hex if session_id is None else UUID(session_id).hex
    report_folder = output / "sessions" / session_id
    session_report: SessionReport | None = None
    accepted_state: PlatformState | None = None
    initial_state: PlatformState | None = None

    def report_for(executor: Executor) -> SessionReport:
        nonlocal session_report
        if session_report is None:
            session_report = SessionReport(
                report_folder, platform_name, executor.platform, protocols
            )
        return session_report

    @asynccontextmanager
    async def lifespan(server):
        nonlocal accepted_state, initial_state
        # Hardware drivers and Qibocal may print; stdout belongs to MCP.
        with redirect_stdout(sys.stderr):
            platform = create_calibration_platform(platform_name)
            if snapshot is not None:
                PlatformState.load(snapshot).apply(platform)
            accepted_state = PlatformState.capture(platform)
            initial_state = (
                PlatformState.load(report_folder / "platform")
                if (report_folder / "platform").exists() else accepted_state
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
        """Platform targets and built-in protocol parameter definitions, with units."""
        executor = ctx.lifespan_context["executor"]
        return json.dumps(
            {
                "platform": platform_name,
                "session_id": session_id,
                "report_folder": str(report_folder),
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
    async def get_platform_state(ctx: Context, path: str = "") -> dict:
        """Inspect current Parameters and Calibration without executing a protocol.

        path is an RFC 6901 JSON pointer; empty returns both complete models.
        Mapping keys are JSON strings. Returns path, value and session metadata.
        """
        async with lock:
            platform = ctx.lifespan_context["executor"].platform
            value = pointer_value(
                PlatformState.capture(platform).document(), pointer_tokens(path),
            )
            return {
                "session_id": session_id, "report_folder": str(report_folder),
                "path": path, "value": value,
            }

    @mcp.tool
    async def patch_platform(changes: dict, ctx: Context) -> dict:
        """Accept explicit scalar edits to Parameters and Calibration for this session.

        changes maps existing RFC 6901 JSON pointers to replacement scalar values.
        Inspect get_platform_state first; supply all coupled edits together.
        Both models are validated before application. Saves an immutable snapshot
        and updates subsequent executions, without overwriting source files.
        For one execution only, use execute_protocol platform_overrides instead.
        """
        nonlocal accepted_state
        async with lock:
            executor = ctx.lifespan_context["executor"]
            state = PlatformState.capture(executor.platform).patched(changes)
            candidate = copy(executor.platform)
            state.apply(candidate)
            with redirect_stdout(sys.stderr):
                report = report_for(executor)
                edit_id = uuid4().hex
                folder = report_folder / "edits" / edit_id
                folder.mkdir(parents=True)
                candidate.dump(folder)
                (folder / "edit.json").write_text(
                    json.dumps({"edit_id": edit_id, "changes": changes}, indent=2),
                    encoding="utf-8",
                )
                report.save(candidate)
            state.apply(executor.platform)
            accepted_state = PlatformState.capture(executor.platform)
            return {
                "session_id": session_id, "report_folder": str(report_folder),
                "edit_id": edit_id, "changes": changes, "snapshot": str(folder),
                "updated": True,
            }

    def find_run(executor: Executor, run_id: str) -> Completed:
        if run_id not in completed_runs and restore_runs:
            if UUID(run_id).hex != run_id:
                raise ValueError("Invalid run_id")
            completed = report_for(executor).find_run(run_id)
            metadata = json.loads(
                (completed.path / "run.json").read_text(encoding="utf-8")
            )
            if metadata["platform"] != platform_name:
                raise ValueError("Run belongs to a different platform")
            completed_runs[run_id] = completed
            if metadata["update_started"]:
                updated_runs.add(run_id)
        if run_id not in completed_runs:
            raise ValueError(f"Unknown run_id in this server session: {run_id}")
        return completed_runs[run_id]

    @mcp.tool
    async def get_notes(ctx: Context, run_id: str | None = None) -> dict:
        """Read session comments, or one execution's comments using its run_id.

        Returns session_id, report_folder, run_id and notes in append order.
        Each note has content, a UTC timestamp and an optional author.
        Reading session notes alone creates no report folder.
        Old run handles expire on a session switch or server exit.
        """
        async with lock:
            executor = ctx.lifespan_context["executor"]
            folder = (
                report_folder if run_id is None else find_run(executor, run_id).path
            )
            return {
                "session_id": session_id, "report_folder": str(report_folder),
                "run_id": run_id,
                "notes": [note.model_dump(mode="json") for note in read_notes(folder)],
            }

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
        note = Note(content=content, author=author)
        async with lock:
            executor = ctx.lifespan_context["executor"]
            completed = None if run_id is None else find_run(executor, run_id)
            with redirect_stdout(sys.stderr):
                report = report_for(executor)
                notes = persist_note(
                    report_folder if completed is None else completed.path, note,
                )
            if completed is None:
                report.output.notes = notes
            else:
                completed.notes = notes
            return {
                "session_id": session_id, "report_folder": str(report_folder),
                "run_id": run_id,
                "notes": [entry.model_dump(mode="json") for entry in notes],
            }

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
        nonlocal session_id, report_folder, session_report, accepted_state, initial_state
        if (source == "run") != (run_id is not None):
            raise ValueError("Supply run_id only and always with source=run")
        if source != "repository" and (
            ref is not None or platform_path is not None or fetch or remote is not None
        ):
            raise ValueError("ref, platform_path, fetch and remote require source=repository")
        if remote is not None and not fetch:
            raise ValueError("remote requires fetch=true")
        if (source == "snapshot") != (snapshot_path is not None):
            raise ValueError("Supply snapshot_path only and always with source=snapshot")
        async with lock:
            executor = ctx.lifespan_context["executor"]
            provenance = {"source": source, "parent_session_id": session_id}
            candidate = copy(executor.platform)
            if source == "current":
                state = accepted_state
            elif source == "initial":
                state = initial_state
            elif source == "snapshot":
                path = Path(snapshot_path).resolve()
                state = PlatformState.load(path)
                provenance["snapshot_path"] = str(path)
            elif source == "repository":
                from qibocal.calibration.platform import locate_platform

                path = (
                    Path(platform_path) if platform_path is not None
                    else locate_platform(platform_name)
                )
                state, revision = await asyncio.to_thread(
                    repository_state, path, ref, fetch=fetch, remote=remote,
                )
                provenance.update(revision)
            else:
                completed = find_run(executor, run_id)
                metadata = json.loads(
                    (completed.path / "run.json").read_text(encoding="utf-8")
                )
                if metadata["update_started"] and not metadata["updated"]:
                    raise ValueError("Run has a failed or incomplete update attempt")
                if completed.results is None:
                    raise ValueError("Run has no fitted results")
                if completed.bound is None or completed.bound.protocol.update is None:
                    raise ValueError("Protocol does not support updating")
                PlatformState.load(completed.path / "execution_platform").apply(candidate)
                with redirect_stdout(sys.stderr):
                    Executor(candidate, update=False).update(completed)
                state = PlatformState.capture(candidate)
                provenance["run_id"] = run_id
            if state is None:
                raise ValueError("Session platform state is not initialized")
            state.apply(candidate)
            new_id = uuid4().hex
            new_folder = output / "sessions" / new_id
            with redirect_stdout(sys.stderr):
                new_report = SessionReport(new_folder, platform_name, candidate, protocols)
                (new_folder / "session.json").write_text(
                    json.dumps(provenance, indent=2), encoding="utf-8"
                )
            state.apply(executor.platform)
            session_id, report_folder, session_report = new_id, new_folder, new_report
            accepted_state = initial_state = state
            completed_runs.clear()
            updated_runs.clear()
            return {
                "session_id": session_id, "report_folder": str(report_folder),
                "snapshot": str(report_folder / "platform"), **provenance,
            }

    @mcp.tool(app=APP)
    async def execute_protocol(
        protocol: str,
        parameters: dict,
        targets: Targets,
        ctx: Context,
        platform_overrides: Overrides = None,
    ) -> ToolResult:
        """Acquire, fit and report, without applying fitted platform updates.

        Choose a built-in protocol and parameters from qibocal://context.
        targets must be qubit IDs, or lists of IDs for multi-qubit protocols.
        Returns a run_id, fitted results, report tables and PNG report images.
        platform_overrides maps existing JSON pointers to scalar trial values.
        Trials use isolated validated models and never change accepted session
        state, even on failure. Hardware effects are not rolled back.
        After each call, show the user the returned images and your fit reading
        in a message before calling another tool.
        """
        if protocol not in protocols:
            raise ValueError(f"Unknown protocol: {protocol}")
        if not targets:
            raise ValueError("Supply at least one target")
        # JSON arrays represent Qibocal's tuple-valued multi-qubit targets.
        selected = [tuple(t) if isinstance(t, list) else t for t in targets]
        async with lock:
            executor = ctx.lifespan_context["executor"]
            execution = executor
            if platform_overrides is not None:
                candidate = copy(executor.platform)
                state = PlatformState.capture(executor.platform).patched(platform_overrides)
                state.apply(candidate)
                execution = Executor(candidate, update=False)
            with redirect_stdout(sys.stderr):
                bound = protocols[protocol](**parameters)
                report = report_for(executor)
                completed = execution(bound, targets=selected)
                run_id = uuid4().hex
                completed = report.add(completed, protocol, parameters, selected)
                folder = completed.path
                execution_snapshot = folder / "execution_platform"
                execution_snapshot.mkdir()
                execution.platform.dump(execution_snapshot)
                metadata = {
                    "run_id": run_id,
                    "session_id": session_id,
                    "report_folder": str(report_folder),
                    "platform": platform_name,
                    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
                    "protocol": protocol,
                    "parameters": parameters,
                    "platform_overrides": platform_overrides,
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
                report.save(executor.platform)
                content: list[ContentBlock] = [
                    TextContent(type="text", text=json.dumps(metadata))
                ]
                reports = completed.reports
                plotted = []
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
                        plotted.append((target, figures))
                        for number, figure in enumerate(figures):
                            stem = folder / f"report-{index}-{number}"
                            figure.write_html(stem.with_suffix(".html"))
                            figure.write_image(
                                stem.with_suffix(".png"), width=1200, height=700
                            )
                            content.append(
                                Image(path=stem.with_suffix(".png")).to_image_content()
                            )
                images = sum(item.type == "image" for item in content)
                if images:
                    # Repeated where it is needed: models act on the latest result.
                    content.append(TextContent(type="text", text=show_images(images)))
                completed_runs[run_id] = completed
                return ToolResult(
                    content=content, structured_content=metadata,
                    meta=figures_meta(plotted),
                )

    @mcp.tool
    async def update_platform(run_id: str, ctx: Context) -> dict:
        """Apply an inspected run's fitted results using Executor.update().

        Updates the live platform used by subsequent executions and writes a
        snapshot under the run folder and the session's new_platform directory.
        Does not overwrite the source platform in QIBOLAB_PLATFORMS.
        A run's update can be attempted only once.
        """
        nonlocal accepted_state
        async with lock:
            executor = ctx.lifespan_context["executor"]
            completed = find_run(executor, run_id)
            if run_id in updated_runs:
                raise ValueError(f"Run already applied or update attempted: {run_id}")
            if completed.results is None:
                raise ValueError("Run has no fitted results")
            if completed.bound is None or completed.bound.protocol.update is None:
                raise ValueError("Protocol does not support updating")
            with redirect_stdout(sys.stderr):
                path = completed.path / "run.json"
                metadata = json.loads(path.read_text(encoding="utf-8"))
                metadata["update_started"] = True
                path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                updated_runs.add(run_id)
                executor.update(completed)
                snapshot = completed.path / "platform"
                snapshot.mkdir()
                executor.platform.dump(snapshot)
                report_for(executor).save(executor.platform)
                completed.task.action.update = True
                # Completed.dump_parameters also replaces potentially stale notes.
                completed.task.dump(completed.path)
                metadata["updated"] = True
                path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                accepted_state = PlatformState.capture(executor.platform)
            return {"run_id": run_id, "updated": True, "snapshot": str(snapshot)}

    return mcp


def main():
    """Run the resolved hardware process without reading ambient configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", dest="platform_name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--restore-runs", action="store_true")
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        server = create_server(**vars(args))
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
