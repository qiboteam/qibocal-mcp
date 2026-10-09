"""Exercise job isolation and state handoff without requiring a SLURM cluster."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import plotly.graph_objects as go
from fastmcp import Client
from fastmcp.exceptions import ToolError
from qibolab import create_platform
from qibocal.auto.operation import Protocol
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration

from qibocal_mcp.slurm import SlurmJobs, create_slurm_server
from qibocal_mcp.worker import run
from test_server import acquire, fit, report, update


class SlurmTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_jobs_restore_only_accepted_state(self):
        platforms = []
        commands = []
        protocol = Protocol(acquire, fit, report, update)

        def create(_):
            dummy = create_platform("dummy")
            platform = CalibrationPlatform(
                **vars(dummy),
                calibration=Calibration(single_qubits={0: QubitCalibration()}),
            )
            platform.connect = Mock(wraps=platform.connect)
            platform.disconnect = Mock(wraps=platform.disconnect)
            platforms.append(platform)
            return platform

        async def start(*command, **kwargs):
            commands.append(command)
            output = Path(command[command.index("--output") + 1])
            snapshot = (
                Path(command[command.index("--snapshot") + 1])
                if "--snapshot" in command else None
            )
            process = SimpleNamespace(returncode=0)

            async def communicate(request):
                try:
                    result = await run(
                        "synthetic",
                        output=output,
                        snapshot=snapshot,
                        request=json.loads(request),
                    )
                except ToolError as error:
                    process.returncode = 1
                    return b"", str(error).encode()
                return result.model_dump_json(by_alias=True).encode(), b""

            process.communicate = communicate
            return process

        def write_image(figure, path, **kwargs):
            Path(path).write_bytes(b"synthetic image")

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.server.create_calibration_platform", side_effect=create),
            patch(
                "qibocal_mcp.server.PROTOCOLS",
                {"synthetic": protocol},
            ),
            patch.object(go.Figure, "write_image", write_image),
            patch("asyncio.create_subprocess_exec", side_effect=start),
        ):
            gateway = create_slurm_server(
                "synthetic",
                partition="qpu",
                output=Path(directory),
                python="/shared/venv/bin/python",
                workdir=Path("/shared/project"),
                slurm_args=["--account=quantum", "--time=00:15:00"],
            )
            async with Client(gateway) as client:
                self.assertEqual(platforms, [])
                self.assertEqual(
                    {tool.name for tool in await client.list_tools()},
                    {"execute_protocol", "update_platform"},
                )
                await client.read_resource("qibocal://context")
                self.assertEqual(len(platforms), 1)
                result = await client.call_tool(
                    "execute_protocol",
                    {"protocol": "synthetic", "parameters": {"value": 5e9}, "targets": [0]},
                )
                self.assertTrue(any(c.type == "image" for c in result.content))
                run_id = result.structured_content["run_id"]
                self.assertIsNone(
                    platforms[-1].calibration.single_qubits[0].qubit.frequency_01
                )
                await client.call_tool("update_platform", {"run_id": run_id})
                self.assertEqual(len(platforms), 3)
                rejected = await client.call_tool(
                    "execute_protocol",
                    {"protocol": "synthetic", "parameters": {"value": 6e9}, "targets": [0]},
                )
                restored = platforms[-1]
                self.assertEqual(
                    restored.calibration.single_qubits[0].qubit.frequency_01, 5e9
                )
                self.assertEqual(
                    restored.config(restored.qubits[0].drive).frequency, 5e9
                )
                self.assertEqual(len(platforms), 4)
                self.assertTrue(all("--snapshot" not in cmd for cmd in commands[:3]))
                self.assertIn("--snapshot", commands[3])
                for command in commands:
                    self.assertEqual(command[0], "srun")
                    self.assertIn("--partition=qpu", command)
                    self.assertIn("--ntasks=1", command)
                    self.assertIn("--account=quantum", command)
                for platform in platforms:
                    platform.connect.assert_called_once()
                    platform.disconnect.assert_called_once()
                with self.assertRaisesRegex(ToolError, "Unknown run_id"):
                    await client.call_tool("update_platform", {"run_id": "unknown"})
                self.assertEqual(len(commands), 4)
                with self.assertRaisesRegex(ToolError, "update attempted"):
                    await client.call_tool("update_platform", {"run_id": run_id})

                def broken_update(results, platform, target):
                    update(results, platform, target)
                    raise RuntimeError("device update failed")

                rejected_id = rejected.structured_content["run_id"]
                with patch.object(protocol, "update", broken_update):
                    with self.assertRaisesRegex(ToolError, "device update failed"):
                        await client.call_tool(
                            "update_platform", {"run_id": rejected_id}
                        )
                with self.assertRaisesRegex(ToolError, "update attempted"):
                    await client.call_tool("update_platform", {"run_id": rejected_id})
                await client.call_tool(
                    "execute_protocol",
                    {"protocol": "synthetic", "parameters": {"value": 7e9}, "targets": [0]},
                )
                self.assertEqual(
                    platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9
                )

    def jobs(self):
        return SlurmJobs(
            "synthetic", partition="qpu", output=Path("runs"),
            python=sys.executable, workdir=Path.cwd(), slurm_args=[],
        )

    async def test_job_failure_is_not_a_success(self):
        process = SimpleNamespace(
            returncode=1, communicate=AsyncMock(return_value=(b"", b"allocation denied"))
        )
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "allocation denied"):
                await self.jobs().call("context", {})

    async def test_cancellation_terminates_job_controller(self):
        process = SimpleNamespace(
            returncode=None,
            communicate=AsyncMock(side_effect=asyncio.CancelledError),
            terminate=Mock(),
            wait=AsyncMock(return_value=0),
        )
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaises(asyncio.CancelledError):
                await self.jobs().call("context", {})
        process.terminate.assert_called_once()
        process.wait.assert_awaited_once()
