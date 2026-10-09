"""Exercise the bridge with the real Executor and synthetic callbacks."""

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import numpy as np
import plotly.graph_objects as go
from fastmcp import Client
from fastmcp.exceptions import ToolError
from qibolab import create_platform
from qibocal.auto.operation import Data, Parameters, Protocol, Results
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration
from qibocal.update import drive_frequency

from qibocal_mcp.server import create_server
from qibocal_mcp.calibration import REPORT_FIGURE_MIME_TYPE


@dataclass
class Inputs(Parameters):
    value: float


@dataclass
class Samples(Data):
    data: dict


@dataclass
class Fit(Results):
    frequency: dict


def acquire(pars: Inputs, targets) -> Samples:
    return Samples({q: np.array([pars.value]) for q in targets})


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
    async def test_client_reports_do_not_render_on_execution_host(self):
        platform = CalibrationPlatform(
            **vars(create_platform("dummy")),
            calibration=Calibration(single_qubits={0: QubitCalibration()}),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.server.create_calibration_platform", return_value=platform),
            patch("qibocal_mcp.server.PROTOCOLS", {"synthetic": Protocol(acquire, fit, report, update)}),
            patch.object(go.Figure, "write_image", side_effect=AssertionError("Remote Chrome invoked")) as render,
        ):
            async with Client(create_server(
                "synthetic", output=Path(directory), report_rendering="client",
            )) as client:
                result = await client.call_tool("execute_protocol", {
                    "protocol": "synthetic", "parameters": {"value": 5e9}, "targets": [0],
                })
                resource = next(item.resource for item in result.content if item.type == "resource")
                self.assertEqual(resource.mimeType, REPORT_FIGURE_MIME_TYPE)
                self.assertIn("data", json.loads(resource.text))
                folder = Path(result.structured_content["folder"])
                self.assertEqual((folder / "report-0-0.json").read_text(), resource.text)
                self.assertTrue((folder / "report-0-0.html").exists())
                self.assertFalse(list(folder.glob("*.png")))
                render.assert_not_called()

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
            patch("qibocal_mcp.server.create_calibration_platform", return_value=platform),
            patch("qibocal_mcp.server.PROTOCOLS", {"synthetic": protocol}),
            patch.object(go.Figure, "write_image", write_image),
        ):
            server = create_server("synthetic", output=Path(directory))
            async with Client(server) as client:
                self.assertEqual(
                    {t.name for t in await client.list_tools()},
                    {"get_calibration_context", "execute_protocol", "update_platform"},
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
                folder = Path(directory) / run_id
                self.assertTrue((folder / "data.npz").exists())
                self.assertTrue((folder / "results.json").exists())
                await client.call_tool("update_platform", {"run_id": run_id})
                self.assertEqual(
                    platform.calibration.single_qubits[0].qubit.frequency_01, 5e9
                )
                self.assertTrue((folder / "platform" / "parameters.json").exists())
                self.assertTrue((folder / "platform" / "calibration.json").exists())
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
