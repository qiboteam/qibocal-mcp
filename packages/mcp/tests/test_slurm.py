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
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ImageContent, TextContent
from qibocal.auto.operation import Protocol
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration
from qibolab import create_platform
from test_server import acquire, fit, report, update

from qibocal_mcp.calibration import PROMPT, instructions_for
from qibocal_mcp.slurm import SlurmJobs, create_slurm_server
from qibocal_mcp.worker import run


class SlurmTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_serves_calibration_prompt_without_jobs(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=AssertionError("The static prompt must not queue a job"),
            ),
        ):
            gateway = create_slurm_server(
                "synthetic", partition="qpu", output=Path(directory),
                python="/shared/bin/python", workdir=Path(directory), slurm_args=[],
            )
            async with Client(gateway) as client:
                prompts = await client.list_prompts()
                self.assertIn(PROMPT, [prompt.name for prompt in prompts])
                prompt = await client.get_prompt(PROMPT)
                self.assertEqual(prompt.messages[0].content.text, instructions_for())
                alias = await client.call_tool(PROMPT, {})
                self.assertEqual(alias.content[0].text, instructions_for())

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
                        session_id=command[command.index("--session-id") + 1],
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
            patch("qibocal_mcp.runtime.create_calibration_platform", side_effect=create),
            patch(
                "qibocal_mcp.runtime.PROTOCOLS",
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
                    {PROMPT, "get_calibration_context", "execute_protocol", "update_platform",
                     "start_session", "get_platform_state", "patch_platform",
                     "append_note", "get_notes"},
                )
                context_text = (
                    await client.read_resource("qibocal://context")
                )[0].text
                self.assertEqual(len(platforms), 1)
                discovery = await client.call_tool("get_calibration_context", {})
                self.assertEqual(len(discovery.content), 1)
                self.assertEqual(discovery.content[0].text, context_text)
                self.assertIn("synthetic", json.loads(context_text)["protocols"])
                self.assertEqual(len(platforms), 2)
                result = await client.call_tool(
                    "execute_protocol",
                    {"protocol": "synthetic", "parameters": {"value": 5e9}, "targets": [0]},
                )
                self.assertTrue(any(c.type == "image" for c in result.content))
                run_id = result.structured_content["run_id"]
                report_folder = Path(result.structured_content["report_folder"])
                original_stats = json.loads((report_folder / "meta.json").read_text())["stats"]
                self.assertIsNone(
                    platforms[-1].calibration.single_qubits[0].qubit.frequency_01
                )
                accepted = await client.call_tool("update_platform", {"run_id": run_id})
                self.assertEqual(len(platforms), 4)
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
                self.assertEqual(len(platforms), 5)
                self.assertTrue(all("--snapshot" not in cmd for cmd in commands[:3]))
                self.assertEqual(
                    commands[3][commands[3].index("--snapshot") + 1],
                    str(report_folder / "platform"),
                )
                self.assertIn("--snapshot", commands[4])
                self.assertEqual(
                    commands[4][commands[4].index("--snapshot") + 1],
                    accepted.structured_content["snapshot"],
                )
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
                self.assertEqual(len(commands), 5)
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
                third = await client.call_tool(
                    "execute_protocol",
                    {"protocol": "synthetic", "parameters": {"value": 7e9}, "targets": [0]},
                )
                self.assertEqual(
                    platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9
                )
                self.assertEqual(
                    json.loads((report_folder / "history.json").read_text()),
                    ["synthetic-0", "synthetic-1", "synthetic-2"],
                )
                meta = json.loads((report_folder / "meta.json").read_text())
                self.assertEqual(meta["stats"]["synthetic-0"], original_stats["synthetic-0"])
                self.assertEqual(len(meta["stats"]), 3)
                initial = Calibration.model_validate_json(
                    (report_folder / "platform" / "calibration.json").read_text()
                )
                latest = Calibration.model_validate_json(
                    (report_folder / "new_platform" / "calibration.json").read_text()
                )
                self.assertIsNone(initial.single_qubits[0].qubit.frequency_01)
                self.assertEqual(latest.single_qubits[0].qubit.frequency_01, 5e9)
                self.assertEqual(len(set(cmd[cmd.index("--session-id") + 1] for cmd in commands)), 1)
                await client.call_tool("update_platform", {
                    "run_id": third.structured_content["run_id"],
                })
                latest = Calibration.model_validate_json(
                    (report_folder / "new_platform" / "calibration.json").read_text()
                )
                previous = Calibration.model_validate_json(
                    (Path(accepted.structured_content["snapshot"]) / "calibration.json").read_text()
                )
                self.assertEqual(latest.single_qubits[0].qubit.frequency_01, 7e9)
                self.assertEqual(previous.single_qubits[0].qubit.frequency_01, 5e9)

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

    async def test_discovery_failures_are_forwarded(self):
        cases = [
            (1, b"", b"allocation denied", "allocation denied"),
            (
                0,
                CallToolResult(
                    content=[TextContent(type="text", text="discovery failed")],
                    isError=True,
                ).model_dump_json(by_alias=True).encode(),
                b"",
                "discovery failed",
            ),
        ]
        for content in (
            [],
            [TextContent(type="text", text="{}")] * 2,
            [ImageContent(type="image", data="AA==", mimeType="image/png")],
        ):
            cases.append(
                (
                    0,
                    CallToolResult(content=content).model_dump_json(by_alias=True).encode(),
                    b"",
                    "invalid context resource",
                )
            )
        for status, stdout, stderr, message in cases:
            with self.subTest(message=message, stdout=stdout):
                process = SimpleNamespace(
                    returncode=status,
                    communicate=AsyncMock(return_value=(stdout, stderr)),
                )
                with patch("asyncio.create_subprocess_exec", return_value=process):
                    gateway = create_slurm_server(
                        "synthetic",
                        partition="qpu",
                        output=Path("runs"),
                        python=sys.executable,
                        workdir=Path.cwd(),
                        slurm_args=[],
                    )
                    async with Client(gateway) as client:
                        with self.assertRaisesRegex(McpError, message):
                            await client.read_resource("qibocal://context")
                        with self.assertRaisesRegex(ToolError, message):
                            await client.call_tool("get_calibration_context", {})
                self.assertEqual(process.communicate.await_count, 2)
                for call in process.communicate.await_args_list:
                    self.assertEqual(
                        json.loads(call.args[0]),
                        {"operation": "context", "arguments": {}},
                    )

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
