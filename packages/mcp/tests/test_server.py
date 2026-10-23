"""Exercise the bridge with the real Executor and synthetic callbacks."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import numpy as np
import plotly
import plotly.graph_objects as go
from fastmcp import Client
from fastmcp.exceptions import ToolError
from qibocal.auto.operation import Data, Parameters, Protocol, Results
from qibocal.auto.output import Output
from qibocal.auto.task import Action
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration
from qibocal.cli.report import generate_figures_and_report
from qibocal.protocols.utils import PowerLevel
from qibocal.update import drive_frequency
from qibolab import create_platform

from qibocal_mcp.calibration import PROMPT, instructions_for, show_images
from qibocal_mcp.plots import FIGURES, VIEW
from qibocal_mcp.runtime import create_server
from qibocal_mcp.server import create_server as create_frontend
from qibocal_mcp.targets import Overrides, Targets


@dataclass
class Inputs(Parameters):
    value: float


@dataclass
class DefaultInputs(Inputs):
    power_level: PowerLevel = PowerLevel.low


@dataclass
class Samples(Data):
    data: dict


@dataclass
class Fit(Results):
    frequency: dict


def acquire(pars: Inputs, targets) -> Samples:
    return Samples({q: np.array([pars.value]) for q in targets})


def acquire_with_defaults(pars: DefaultInputs, targets) -> Samples:
    return acquire(pars, targets)


def fit(data: Samples) -> Fit:
    return Fit({q: float(data[q][0]) for q in data.qubits})


def report(data, target, fit):
    return [go.Figure(go.Scatter(y=data[target]))], f"Frequency: {fit.frequency[target]}"


def update(results, platform, target):
    platform.calibration.single_qubits[target].qubit.frequency_01 = (
        results.frequency[target]
    )
    drive_frequency(results.frequency[target], platform, target)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_frontend_preserves_schemas_reports_updates_and_errors(self):
        platform = CalibrationPlatform(
            **vars(create_platform("dummy")),
            calibration=Calibration(single_qubits={0: QubitCalibration()}),
        )
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
        )

        def write_image(figure, file, **kwargs):
            Path(file).write_bytes(png)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.runtime.create_calibration_platform", return_value=platform),
            patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": Protocol(acquire, fit, report, update)}),
            patch.object(go.Figure, "write_image", write_image),
        ):
            backend = create_server("synthetic", output=Path(directory))
            with patch("qibocal_mcp.server.StdioTransport", return_value=backend):
                async with Client(backend) as original, Client(create_frontend("synthetic")) as client:
                    self.assertEqual(
                        [tool.model_dump() for tool in await client.list_tools()],
                        [tool.model_dump() for tool in await original.list_tools()],
                    )
                    self.assertEqual(
                        [prompt.model_dump() for prompt in await client.list_prompts()],
                        [prompt.model_dump() for prompt in await original.list_prompts()],
                    )
                    self.assertEqual(
                        await client.read_resource("qibocal://context"),
                        await original.read_resource("qibocal://context"),
                    )
                    result = await client.call_tool("execute_protocol", {
                        "protocol": "synthetic", "parameters": {"value": 5e9}, "targets": [0],
                    })
                    self.assertTrue(any(item.type == "image" for item in result.content))
                    # The result ends asking the model to show the images to the user.
                    self.assertEqual(result.content[-1].text, show_images(1))
                    # Interactive figures travel beside the images, for MCP Apps.
                    plotted = result.meta[FIGURES]
                    self.assertEqual(plotted["plotly"], plotly.offline.get_plotlyjs_version())
                    self.assertEqual(plotted["figures"][0]["target"], "0")
                    self.assertEqual(plotted["figures"][0]["data"][0]["type"], "scatter")
                    execute = next(
                        tool for tool in await client.list_tools()
                        if tool.name == "execute_protocol"
                    )
                    self.assertEqual(execute.meta["ui"]["resourceUri"], VIEW)
                    view = (await client.read_resource(VIEW))[0]
                    self.assertEqual(view.mimeType, "text/html;profile=mcp-app")
                    self.assertIn("ui/notifications/tool-result", view.text)
                    self.assertIn(FIGURES, view.text)
                    run_id = result.structured_content["run_id"]
                    applied = await client.call_tool("update_platform", {"run_id": run_id})
                    self.assertTrue(applied.structured_content["updated"])
                    self.assertEqual(platform.calibration.single_qubits[0].qubit.frequency_01, 5e9)
                    with self.assertRaisesRegex(ToolError, "already applied"):
                        await client.call_tool("update_platform", {"run_id": run_id})

    async def test_targets_match_qibocal_and_tolerate_text(self):
        from pydantic import TypeAdapter, ValidationError
        from qibocal.auto.task import Targets as QibocalTargets

        adapter = TypeAdapter(Targets)
        original = TypeAdapter(QibocalTargets)
        for targets in ([0], ["0"], ["a"], [[0, 1]], [[0, 1, 2]], []):
            # Nested lists become Qibocal's tuples in execute_protocol.
            selected = [
                tuple(t) if isinstance(t, list) else t
                for t in adapter.validate_python(targets)
            ]
            self.assertEqual(selected, original.validate_python(targets))
        # Clients that send JSON as text, or a single target, are understood.
        for text, expected in (("[2]", [2]), ("2", [2]), (2, [2]), ("q2", ["q2"]),
                               ("[[0, 1]]", [[0, 1]])):
            self.assertEqual(adapter.validate_python(text), expected)
        with self.assertRaises(ValidationError):
            adapter.validate_python([0, [0, 1]])
        overrides = TypeAdapter(Overrides)
        self.assertEqual(overrides.validate_python('{"/a": 1}'), {"/a": 1})
        self.assertIsNone(overrides.validate_python(None))

    async def test_tool_arguments_declare_plain_json_types(self):
        """Clients may send a field as text when its top level is an anyOf."""
        async with Client(create_frontend("synthetic")) as client:
            execute = next(
                tool for tool in await client.list_tools()
                if tool.name == "execute_protocol"
            )
        properties = execute.inputSchema["properties"]
        self.assertEqual(properties["targets"]["type"], "array")
        self.assertEqual(properties["platform_overrides"]["type"], "object")

    async def test_calibration_prompt_and_alias_tool_need_no_hardware(self):
        platform = CalibrationPlatform(
            **vars(create_platform("dummy")),
            calibration=Calibration(single_qubits={0: QubitCalibration()}),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.runtime.create_calibration_platform", return_value=platform),
            patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": Protocol(acquire, fit, report, update)}),
        ):
            async with Client(create_server("synthetic", output=Path(directory))) as client:
                prompts = await client.list_prompts()
                self.assertIn(PROMPT, [prompt.name for prompt in prompts])
                prompt = await client.get_prompt(PROMPT)
                self.assertEqual(prompt.messages[0].content.text, instructions_for())
                alias = next(
                    tool for tool in await client.list_tools() if tool.name == PROMPT
                )
                self.assertIn("start here", alias.description)
                result = await client.call_tool(PROMPT, {})
                self.assertEqual(result.content[0].text, instructions_for())

    async def test_execute_inspect_update(self):
        dummy = create_platform("dummy")
        platform = CalibrationPlatform(
            **vars(dummy),
            calibration=Calibration(single_qubits={0: QubitCalibration()}),
        )
        protocol = Protocol(acquire, fit, report, update)
        # A valid tiny PNG; the full image renderer is exercised separately.
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
        )

        def write_image(figure, file, **kwargs):
            Path(file).write_bytes(png)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.runtime.create_calibration_platform", return_value=platform),
            patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": protocol}),
            patch.object(go.Figure, "write_image", write_image),
        ):
            server = create_server("synthetic", output=Path(directory))
            async with Client(server) as client:
                self.assertEqual(
                    {t.name for t in await client.list_tools()},
                    {PROMPT, "get_calibration_context", "execute_protocol", "update_platform",
                     "start_session", "get_platform_state", "patch_platform",
                     "append_note", "get_notes"},
                )
                context_text = (
                    await client.read_resource("qibocal://context")
                )[0].text
                discovery = await client.call_tool("get_calibration_context", {})
                self.assertEqual(len(discovery.content), 1)
                self.assertEqual(discovery.content[0].text, context_text)
                context = json.loads(context_text)
                self.assertEqual(context["qubits"], list(platform.qubits))
                self.assertIn(
                    "value: float", context["protocols"]["synthetic"]["parameters"]
                )
                result = await client.call_tool(
                    "execute_protocol",
                    {
                        "protocol": "synthetic",
                        "parameters": {"value": 5e9},
                        "targets": [0],
                    },
                )
                self.assertTrue(any(c.type == "image" for c in result.content))
                self.assertIsNone(
                    platform.calibration.single_qubits[0].qubit.frequency_01
                )
                run_id = result.structured_content["run_id"]
                folder = Path(result.structured_content["folder"])
                report_folder = Path(result.structured_content["report_folder"])
                self.assertEqual(
                    report_folder,
                    Path(directory).resolve() / "sessions" / result.structured_content["session_id"],
                )
                self.assertEqual(folder, report_folder / "data" / "synthetic-0")
                self.assertTrue((folder / "data.npz").exists())
                self.assertTrue((folder / "results.json").exists())
                self.assertEqual(Action.load(folder).parameters["nshots"], platform.settings.nshots)
                self.assertFalse(Action.load(folder).update)
                await client.call_tool("update_platform", {"run_id": run_id})
                self.assertEqual(
                    platform.calibration.single_qubits[0].qubit.frequency_01, 5e9
                )
                self.assertTrue((folder / "platform" / "parameters.json").exists())
                self.assertTrue((folder / "platform" / "calibration.json").exists())
                self.assertTrue(Action.load(folder).update)
                self.assertTrue(json.loads((folder / "run.json").read_text())["updated"])
                with self.assertRaisesRegex(ToolError, "already applied"):
                    await client.call_tool("update_platform", {"run_id": run_id})
                with self.assertRaisesRegex(ToolError, "Unknown run_id"):
                    await client.call_tool("update_platform", {"run_id": "missing"})
                with self.assertRaisesRegex(ToolError, "Unknown protocol"):
                    await client.call_tool(
                        "execute_protocol",
                        {"protocol": "missing", "parameters": {}, "targets": [0]},
                    )
                second = await client.call_tool("execute_protocol", {
                    "protocol": "synthetic", "parameters": {"value": 6e9}, "targets": [0],
                })
                self.assertEqual(second.structured_content["report_folder"], str(report_folder))
                self.assertEqual(second.structured_content["session_id"], result.structured_content["session_id"])
                self.assertEqual(Path(second.structured_content["folder"]), report_folder / "data" / "synthetic-1")
                self.assertNotEqual(second.structured_content["run_id"], run_id)
                self.assertEqual(json.loads((report_folder / "history.json").read_text()), ["synthetic-0", "synthetic-1"])
                initial = Calibration.model_validate_json(
                    (report_folder / "platform" / "calibration.json").read_text()
                )
                latest = Calibration.model_validate_json(
                    (report_folder / "new_platform" / "calibration.json").read_text()
                )
                self.assertIsNone(initial.single_qubits[0].qubit.frequency_01)
                self.assertEqual(latest.single_qubits[0].qubit.frequency_01, 5e9)
                with patch("qibocal.protocols.synthetic", protocol, create=True):
                    loaded = Output.load(report_folder)
                self.assertEqual([str(task_id) for task_id in loaded.history], ["synthetic-0", "synthetic-1"])
                self.assertEqual(loaded.meta.targets, [0])
                self.assertIsNotNone(loaded.meta.start_time)
                self.assertGreaterEqual(loaded.meta.end_time, loaded.meta.start_time)
                self.assertEqual(loaded.history["synthetic"][0].results.frequency[0], 5e9)
                self.assertEqual(loaded.history["synthetic"][1].results.frequency[0], 6e9)
                self.assertGreater(loaded.meta.stats["synthetic-0"]["acquisition"], 0)
                self.assertGreater(loaded.meta.stats["synthetic-1"]["fit"], 0)
                figures, table = generate_figures_and_report(
                    loaded.history["synthetic"][0], 0
                )
                self.assertEqual(len(figures), 1)
                self.assertEqual(table, "Frequency: 5000000000.0")

    async def test_sessions_are_separate_and_discovery_creates_no_report(self):
        platform = CalibrationPlatform(
            **vars(create_platform("dummy")),
            calibration=Calibration(single_qubits={0: QubitCalibration()}),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.runtime.create_calibration_platform", return_value=platform),
            patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": Protocol(acquire)}),
        ):
            reports = []
            for _ in range(2):
                async with Client(create_server("synthetic", output=Path(directory))) as client:
                    context = json.loads((await client.call_tool("get_calibration_context", {})).content[0].text)
                    self.assertFalse(Path(context["report_folder"]).exists())
                    result = await client.call_tool("execute_protocol", {
                        "protocol": "synthetic", "parameters": {"value": 5e9}, "targets": [0],
                    })
                    folder = Path(result.structured_content["folder"])
                    reports.append(Path(result.structured_content["report_folder"]))
                    self.assertEqual(result.structured_content["session_id"], context["session_id"])
                    self.assertIsNone(result.structured_content["results"])
                    # Without images, there is nothing to show.
                    self.assertFalse(any(
                        "cannot see tool results" in getattr(item, "text", "")
                        for item in result.content
                    ))
                    self.assertFalse((folder / "results.json").exists())
                    self.assertEqual(Action.load(folder).targets, [0])
                    if len(reports) == 1:
                        old_run = result.structured_content["run_id"]
                    else:
                        with self.assertRaisesRegex(ToolError, "Unknown run_id"):
                            await client.call_tool("update_platform", {"run_id": old_run})
            self.assertNotEqual(reports[0], reports[1])
            for folder in reports:
                self.assertEqual(json.loads((folder / "history.json").read_text()), ["synthetic-0"])

    async def test_mixed_protocol_order_and_pair_targets(self):
        platform = CalibrationPlatform(
            **vars(create_platform("dummy")),
            calibration=Calibration(single_qubits={0: QubitCalibration()}),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.runtime.create_calibration_platform", return_value=platform),
            patch("qibocal_mcp.runtime.PROTOCOLS", {
                "single": Protocol(acquire_with_defaults),
                "pair": Protocol(acquire, two_qubit_gates=True),
            }),
        ):
            async with Client(create_server("synthetic", output=Path(directory))) as client:
                for protocol, targets in [("single", [0]), ("pair", [[0, 1]]), ("single", [1])]:
                    result = await client.call_tool("execute_protocol", {
                        "protocol": protocol, "parameters": {"value": 5e9}, "targets": targets,
                    })
                    self.assertEqual(Action.load(Path(result.structured_content["folder"])).targets, targets)
                folder = Path(result.structured_content["report_folder"])
                self.assertEqual(json.loads((folder / "history.json").read_text()), ["single-0", "pair-0", "single-1"])
                self.assertEqual(json.loads((folder / "meta.json").read_text())["targets"], [0, 1])
