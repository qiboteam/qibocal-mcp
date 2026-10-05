"""Stateful MCP server for autonomous qibocal calibration."""

import json
import shutil
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from qibolab.platform import locate_platform

from calibration_mcp_servers.utils import (
    make_output_path,
    make_runcard,
    read_platform_json,
    return_content,
    run_qq,
    update_qq,
    write_runcard,
)

mcp = FastMCP("qibocal-calibrator")


#######################################################################
# Helper classes and functions
#######################################################################


@dataclass
class CalibrationSession:
    """Private, on-disk state of a calibration session.

    Each `run_protocol` call is its own `qq run` subprocess, writing into a fresh
    step folder under `path`; no hardware connection is kept between calls.
    """

    platform_name: str
    targets: list[Any]
    path: Path
    partition: str | None
    step_counter: int = 0
    latest_step_folder: Path | None = None
    latest_step_targets: list[Any] | None = None
    latest_operation: str | None = None
    manual_override_operation: str | None = None
    manual_override_targets: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Create a session-local platform copy that is isolated from the real registry."""
        self.step_counter = 0
        self.latest_step_folder = None
        self.latest_step_targets = None
        self.latest_operation = None
        self.manual_override_operation = None
        self.manual_override_targets = []
        self.session_platform_name = "new_platform"
        self.session_platform_path = self.path / self.session_platform_name

        # copy the source platform to the session-local platform path
        source_platform_path = locate_platform(self.platform_name).absolute()
        shutil.copytree(source_platform_path, self.session_platform_path)

    def update_session_platform(
        self, new_platform_path: Path, skip_qubits: list[Any] | None = None
    ) -> None:
        """Replace the session-local platform with an updated version.

        Update the session-local platform from the given folder, optionally
        excluding the qubits listed in `skip_qubits`. The real platform
        registry is left unchanged.
        """
        update_qq(
            data_path=new_platform_path,
            platform_path=self.path,
            skip_qubits=skip_qubits,
        )


_active_session: CalibrationSession | None = None


def _session() -> CalibrationSession:
    if _active_session is None:
        raise RuntimeError("No calibration is active. Call start_calibration first.")
    return _active_session


def _close_session(*, accept_latest_platform: bool) -> dict[str, Any] | None:
    """Close the active session, leaving the persisted step data in place."""
    global _active_session
    session = _active_session
    if session is None:
        return None

    try:
        if accept_latest_platform:
            try:
                assert (
                    session.latest_step_folder and session.latest_step_folder.is_dir()
                ), "Latest step folder is not available or is not a directory."
                session.update_session_platform(session.latest_step_folder)
            except AssertionError as e:
                print(f"Failed to update session platform: {e}")

        return {
            "data_folder": str(session.path.absolute()),
            "platform": session.platform_name,
            "step_counter": session.step_counter,
            "session_platform": str(session.session_platform_path.absolute()),
        }
    finally:
        _active_session = None


def _analyze_platform_architecture(platform_name: str) -> dict[str, Any]:
    """Inspect a platform's `parameters.json` to describe its QPU architecture.

    Reports the qubit names, the connectivity topology (from the two-qubit
    native gates), which qubits are flux tunable (have a flux channel in
    `configs`), and which couplers are present (flux channels named
    `coupler_*`).
    """
    parameters_path = locate_platform(platform_name) / "parameters.json"
    parameters = json.loads(parameters_path.read_text(encoding="utf-8"))

    configs = parameters.get("configs", {})
    native_gates = parameters.get("native_gates", {})

    qubit_names = sorted(
        (
            name
            for name in native_gates.get("single_qubit", {})
            if any(channel.startswith(f"{name}/") for channel in configs)
        ),
        key=str,
    )
    flux_channels = {
        name: config for name, config in configs.items() if name.endswith("/flux")
    }
    qubit_flux = {
        name: config
        for name, config in flux_channels.items()
        if not name.startswith("coupler")
    }
    coupler_flux = {
        name: config
        for name, config in flux_channels.items()
        if name.startswith("coupler")
    }

    topology: dict[str, list[Any]] = {}
    for pair in native_gates.get("two_qubit", {}):
        a, _, b = str(pair).partition("-")
        topology.setdefault(a, []).append(b)
        topology.setdefault(b, []).append(a)

    return {
        "platform": platform_name,
        "parameters_file": str(parameters_path.absolute()),
        "qubits": qubit_names,
        "nqubits": len(qubit_names),
        "topology": {
            qubit: sorted(neighbors, key=str) for qubit, neighbors in topology.items()
        },
        "flux_tunable_qubits": sorted(qubit_flux, key=str),
        "couplers": sorted(coupler_flux, key=str),
        "has_couplers": bool(coupler_flux),
        "has_flux_tunable_qubits": bool(qubit_flux),
    }


def _run_targets(
    session: CalibrationSession,
    step_id: int,
    operation: str,
    parameters: dict[str, Any],
    targets: list[Any],
    output_path: Path,
) -> dict[str, Any]:
    """Run one runcard and Slurm job for the given targets."""
    runcard = make_runcard(
        [
            {
                "id": str(step_id),
                "operation": operation,
                "parameters": parameters,
                "update": True,
            }
        ],
        targets=targets,
        platform=session.session_platform_name,
        update=True,
    )
    runcard_path = write_runcard(runcard, output_dir=output_path)
    try:
        run_qq(
            runcard_path,
            output_path,
            update=True,
            partition=session.partition,
            platform_path=session.path,
        )
    finally:
        runcard_path.unlink(missing_ok=True)

    return return_content(output_path)


def calibration_report(report_path: str) -> str:
    """Render a run's HTML report with fit plots and results for visual review.

    The report path is supplied as the `path` query parameter in the resource URI.
    The existing HTML plots and serialized results are exposed for visual review.
    """
    path = Path(report_path).expanduser().absolute()
    report_html_path = path / "index.html"
    if not path.is_dir() or not report_html_path.is_file():
        raise FileNotFoundError(
            f"Expected a qibocal report folder containing index.html: {path}"
        )

    report_html = report_html_path.read_text(encoding="utf-8")
    results_sections = []
    for results_path in sorted((path / "data").glob("*/results.json")):
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
            result_text = json.dumps(results, indent=2, sort_keys=True)
        except (OSError, json.JSONDecodeError) as error:
            result_text = f"Unable to read fit results: {error}"
        results_sections.append(
            "<details>"
            f"<summary>{escape(results_path.parent.name)} fit results</summary>"
            f"<pre>{escape(result_text)}</pre>"
            "</details>"
        )

    review_section = (
        '<section id="fit-review"><h1>Fit review</h1>'
        "<p>Visually compare each measured trace with its fitted model. Check that "
        "the feature is resolved across the sweep, residual deviations are small "
        "and unsystematic, and fitted values are physically plausible. A serialized "
        "fit result alone does not establish fit quality.</p>"
        f"{''.join(results_sections)}"
        "</section>"
    )
    review_styles = (
        "<style>#fit-review{font-family:sans-serif;margin:2rem auto;max-width:1200px}"
        "#fit-review pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
    )
    if "</body>" in report_html:
        return report_html.replace(
            "</body>", f"{review_styles}{review_section}</body>", 1
        )
    return f"{report_html}{review_styles}{review_section}"


#######################################################################
# Tools
#######################################################################


@mcp.tool()
def read_protocol_catalog(filename: str) -> dict[str, str]:
    """Read a protocol catalog markdown file for calibration planning."""
    return {"catalog": Path(__file__).with_name(filename).read_text(encoding="utf-8")}


@mcp.tool()
def read_calibration_report(report_path: str) -> dict[str, str]:
    """Read a run's HTML report with fit plots and results for visual review."""
    return {"report": calibration_report(report_path)}


@mcp.tool()
def get_platform_architecture(platform_name: str) -> dict[str, Any]:
    """Describe the QPU architecture.

    Call this BEFORE starting the calibration. It parses the
    platform's `parameters.json` and reports: the qubit names and count, the
    connectivity topology (which qubits are connected, from the two-qubit
    native gates), which qubits are flux tunable (they have a flux channel in
    `configs`), and which couplers are present (flux channels named
    `coupler_*`).
    """
    return _analyze_platform_architecture(platform_name)


@mcp.tool()
def read_platform_files() -> dict[str, Any]:
    """Read the session's last saved platform `calibration.json` and `parameters.json`.

    Returns the parsed contents of both files from the session-local platform
    (the one updated by `accept_step_platform`) so the current calibrated
    values and the platform definition can be inspected together.
    """
    try:
        session = _session()
        platform_path = session.session_platform_path
        return {"platform": session.platform_name} | read_platform_json(platform_path)
    except FileNotFoundError as e:
        _close_session(accept_latest_platform=False)
        raise FileNotFoundError(f"Failed to read platform files: {e}")


@mcp.tool()
def start_calibration(
    parent_folder: str,
    targets: list[Any],
    platform: str,
    partition: str | None = None,
) -> dict[str, Any]:
    """Start one calibration session.

    ``partition`` selects the Slurm partition every subsequent `run_protocol` call
    is submitted to (via ``sbatch --wait --time=01:00:00``); when omitted those
    calls run on the local host.
    """
    global _active_session
    # close any existing session before starting a new one
    _close_session(accept_latest_platform=False)

    path = make_output_path(parent_folder, platform)
    _active_session = CalibrationSession(
        platform_name=platform,
        targets=targets,
        path=path,
        partition=partition,
    )
    return {
        "data_folder": str(path.absolute()),
        "targets": targets,
        "platform": platform,
        "working_platform": str(_active_session.session_platform_path.absolute()),
    }


@mcp.tool()
def run_protocol(
    operation: str,
    parameters: dict[str, Any],
    targets: list[Any] | None = None,
) -> dict[str, Any]:
    """Run one protocol in parallel across the selected targets."""
    try:
        session = _session()

        if session.manual_override_operation is not None:
            requested_targets = list(session.targets if targets is None else targets)
            if operation != session.manual_override_operation or set(
                requested_targets
            ) != set(session.manual_override_targets):
                return {
                    "error": (
                        "A manual platform estimate requires the next run to repeat "
                        f"{session.manual_override_operation!r} for exactly "
                        f"{session.manual_override_targets!r} with focused parameters."
                    ),
                    "required_operation": session.manual_override_operation,
                    "required_targets": session.manual_override_targets,
                }

        step_id = session.step_counter
        session.step_counter += 1

        step_targets = list(session.targets if targets is None else targets)
        targets_str = "-".join(str(target) for target in step_targets)

        step_path = session.path / f"{step_id}-{operation}-qubits-{targets_str}"
        run = _run_targets(
            session, step_id, operation, parameters, step_targets, step_path
        )

        run.update(
            {
                "operation": operation,
                "step_id": step_id,
                "targets": step_targets,
                "session_platform": str(session.session_platform_path.absolute()),
            }
        )

        session.latest_step_folder = step_path
        session.latest_step_targets = step_targets
        session.latest_operation = operation
        session.manual_override_operation = None
        session.manual_override_targets = []

        return run
    except BaseException:
        _close_session(accept_latest_platform=True)
        raise


@mcp.tool()
def accept_step_platform(accepted_qubits: list[Any]) -> dict[str, Any]:
    """Promote successful qubit updates to the next calibration iteration.

    After visually checking each qubit's HTML report plot, pass only the qubits
    whose fits match the measured signal and physical expectation. Updates for all
    other qubits in the latest step are discarded.
    """
    try:
        session = _session()

        step_path = session.latest_step_folder
        if step_path is None:
            return {"error": "No step has been run yet."}
        step_targets = session.latest_step_targets
        if step_targets is None:
            return {"error": "The latest step has no recorded targets."}

        unknown_qubits = set(accepted_qubits) - set(step_targets)
        if unknown_qubits:
            return {
                "error": f"Accepted qubits were not part of the latest step: {sorted(unknown_qubits, key=str)}"
            }

        if not (step_path / "new_platform").exists():
            return {
                "error": f"No updated platform was produced in {step_path}; missing platform folder."
            }

        skipped_qubits = [
            qubit for qubit in step_targets if qubit not in accepted_qubits
        ]

        session.update_session_platform(
            new_platform_path=step_path, skip_qubits=skipped_qubits
        )

        return {
            "source_step": str(step_path.absolute()),
            "platform": session.platform_name,
            "accepted_qubits": accepted_qubits,
            "skipped_qubits": skipped_qubits,
        }
    except BaseException:
        _close_session(accept_latest_platform=False)
        raise


@mcp.tool()
def manually_overwrite_qubits(
    manual_overwritten_qubits: list[Any], operation: str
) -> dict[str, Any]:
    """Manually overwrite specific qubits for a given experiment."""
    try:
        session = _session()

        assert (
            session.latest_step_folder
            and session.latest_step_folder.is_dir()
            and session.latest_step_targets
            and session.latest_operation
        ), "No step has been run yet."

        assert set(manual_overwritten_qubits) <= set(session.latest_step_targets)
        assert session.latest_operation == operation, (
            "The provided operation does not match the latest recorded operation."
        )

        session.manual_override_operation = operation
        session.manual_override_targets = manual_overwritten_qubits

        return {
            "operation": operation,
            "manually_overwritten_qubits": manual_overwritten_qubits,
        }
    except BaseException:
        _close_session(accept_latest_platform=False)
        raise


@mcp.tool()
def abort_calibration() -> dict[str, Any]:
    """Abort the active session without accepting the latest step platform."""
    if _active_session is None:
        return {"error": "No calibration is active. Call start_calibration first."}
    return {**_close_session(accept_latest_platform=False), "aborted": True}  # type: ignore[arg-type]


@mcp.tool()
def finish_calibration() -> dict[str, Any]:
    """Close the session and return a summary of its steps."""
    if _active_session is None:
        return {"error": "No calibration is active. Call start_calibration first."}
    return _close_session(accept_latest_platform=True)  # type: ignore[return-value]


#######################################################################
# Start the MCP server (stdio transport).
#######################################################################


def calibrator_start() -> None:
    mcp.run(transport="stdio")
