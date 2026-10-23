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
from qibocal_mcp.plots import FIGURE_TOOL, VIEW, reminder
from qibocal_mcp.progress import MARKER
from qibocal_mcp.slurm import (
    LISTED,
    SlurmJobs,
    create_slurm_server,
    job_name,
    queue_summary,
)
from qibocal_mcp.worker import run


def piped(process):
    """Serve a fake process's communicate(request) through the worker's pipes.

    communicate may set process.returncode, which wait() then reports.
    """
    code, process.returncode = process.returncode, None
    process.final = None
    process.stdout, process.stderr = asyncio.StreamReader(), asyncio.StreamReader()
    request, finished = bytearray(), asyncio.Event()
    process.terminated = 0

    async def serve():
        try:
            stdout, stderr = await process.communicate(bytes(request))
        except Exception as error:
            # As with communicate() itself, the caller sees the error.
            process.stdout.set_exception(error)
            process.stderr.set_exception(error)
            return
        finally:
            # Running until wait() reports; communicate may set its status.
            process.final = code if process.returncode is None else process.returncode
            process.returncode = None
        process.stdout.feed_data(stdout)
        process.stderr.feed_data(stderr)
        process.stdout.feed_eof()
        process.stderr.feed_eof()
        finished.set()

    def close():
        process.serving = asyncio.ensure_future(serve())

    def terminate():
        process.terminated += 1
        process.serving.cancel()
        process.final = -15
        finished.set()

    async def wait():
        await finished.wait()
        process.returncode = process.final
        return process.final

    async def drain():
        pass

    process.stdin = SimpleNamespace(write=request.extend, drain=drain, close=close)
    process.terminate = process.kill = terminate
    process.wait = wait
    return process


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
                        run_id=command[command.index("--run-id") + 1] if "--run-id" in command else None,
                    )
                except ToolError as error:
                    process.returncode = 1
                    return b"", str(error).encode()
                return result.model_dump_json(by_alias=True).encode(), b""

            process.communicate = communicate
            return piped(process)

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
                     "append_note", "get_notes", "get_run", "cancel_run",
                     "get_run_results", "get_run_figures",
                     "get_protocol", "search_platform_state",
                     "list_versions", "diff_platform", "save_platform", "checkout_platform"},
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
                self.assertEqual(
                    result.content[0].text, reminder(result.structured_content["plots"], False),
                )
                execute = next(
                    tool for tool in await client.list_tools()
                    if tool.name == "execute_protocol"
                )
                self.assertEqual(execute.meta["ui"]["resourceUri"], VIEW)
                self.assertIn(
                    VIEW, [str(resource.uri) for resource in await client.list_resources()]
                )
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
                # Figures are read from the run folder by a local worker, on request.
                figures = (await client.call_tool(
                    FIGURE_TOOL, {"run_id": run_id},
                )).structured_content["figures"]
                self.assertEqual(figures["0"]["data"][0]["type"], "scatter")
                # Only acquisitions are SLURM jobs, and only they connect.
                for command, platform in zip(commands, platforms, strict=True):
                    acquisition = "--run-id" in command
                    self.assertEqual(command[0] == "srun", acquisition)
                    if acquisition:
                        self.assertIn("--partition=qpu", command)
                        self.assertIn("--ntasks=1", command)
                        self.assertIn("--account=quantum", command)
                    self.assertEqual(platform.connect.call_count, int(acquisition))
                    platform.disconnect.assert_called_once()
                with self.assertRaisesRegex(ToolError, "Unknown run_id"):
                    await client.call_tool("update_platform", {"run_id": "unknown"})
                self.assertEqual(len(commands), 6)
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
        process = piped(SimpleNamespace(
            returncode=1, communicate=AsyncMock(return_value=(b"", b"allocation denied"))
        ))
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
                communicate = AsyncMock(return_value=(stdout, stderr))
                with patch(
                    "asyncio.create_subprocess_exec",
                    side_effect=lambda *command, **kwargs: piped(SimpleNamespace(
                        returncode=status, communicate=communicate,
                    )),
                ):
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
                self.assertEqual(communicate.await_count, 2)
                for call in communicate.await_args_list:
                    self.assertEqual(
                        json.loads(call.args[0]),
                        {"operation": "context", "arguments": {}},
                    )

    async def test_queued_runs_report_progress_and_notes_need_no_job(self):
        started, release, commands = asyncio.Event(), asyncio.Event(), []
        metadata = {"run_id": "abc", "report_folder": "/runs/sessions/s"}

        def start(*command, **kwargs):
            if command[0] == "squeue":
                name = job_name(commands[0][commands[0].index("--run-id") + 1])
                listing = (
                    "7|RUNNING|alice|calibrate|1:02:03|2:00:00|None|900\n"
                    f"8|PENDING|bob|{name}|0:00|1:00:00|Resources|500\n"
                    "9|PENDING|carol|sweep|0:00|1:00:00|Priority|800\n"
                    "10|PENDING|dave|late|0:00|1:00:00|Priority|500\n"
                )
                return SimpleNamespace(
                    returncode=0, communicate=AsyncMock(return_value=(listing.encode(), b"")),
                )
            commands.append(command)
            process = SimpleNamespace(returncode=0)

            async def communicate(request):
                if command[0] != "srun":
                    notes = {"report_folder": "/runs/sessions/s", "notes": []}
                    result = CallToolResult(content=[], structuredContent=notes)
                    return result.model_dump_json(by_alias=True).encode(), b""
                await started.wait()
                process.stderr.feed_data(
                    (MARKER + json.dumps({"executions": 3, "estimated_s": 120.0})
                     + "\n").encode()
                )
                await release.wait()
                result = CallToolResult(content=[], structuredContent=metadata)
                return result.model_dump_json(by_alias=True).encode(), b""

            process.communicate = communicate
            return piped(process)

        with patch("asyncio.create_subprocess_exec", side_effect=start):
            gateway = create_slurm_server(
                "synthetic", partition="qpu", output=Path("runs"),
                python=sys.executable, workdir=Path.cwd(), slurm_args=[],
            )
            async with Client(gateway) as client:
                queued = (await client.call_tool("execute_protocol", {
                    "protocol": "synthetic", "parameters": {}, "targets": [0],
                    "wait": 0.1,
                })).structured_content
                self.assertEqual(queued["status"], "queued")
                self.assertEqual(queued["queue"], {
                    "job": {"job_id": "8", "state": "PENDING", "reason": "Resources"},
                    "running": 1, "pending": 2, "pending_ahead": 1,
                    "running_jobs": [{
                        "job_id": "7", "user": "alice", "name": "calibrate",
                        "elapsed": "1:02:03", "time_limit": "2:00:00",
                    }],
                })
                run_id = queued["run_id"]
                self.assertEqual(commands[0][commands[0].index("--run-id") + 1], run_id)
                await client.call_tool("append_note", {"content": "Queued, waiting"})
                self.assertNotEqual(commands[-1][0], "srun")
                started.set()
                running = (await client.call_tool(
                    "get_run", {"run_id": run_id, "wait": 0.2},
                )).structured_content
                self.assertEqual(running["status"], "running")
                self.assertNotIn("queue", running)
                self.assertEqual((running["executions"], running["estimated_s"]), (3, 120.0))
                with self.assertRaisesRegex(ToolError, "in progress"):
                    await client.call_tool("update_platform", {"run_id": "abc"})
                release.set()
                done = await client.call_tool("get_run", {"run_id": run_id, "wait": 5})
                self.assertEqual(done.structured_content, metadata)
        self.assertEqual([command[0] == "srun" for command in commands], [True, False])

    def test_queue_summary_lists_few_running_jobs_and_tolerates_noise(self):
        running = "".join(f"{i}|RUNNING|u|j|0:01|1:00|None|1\n" for i in range(LISTED + 1))
        summary = queue_summary("garbage\n" + running, job_name("x"))
        self.assertEqual((summary["job"], summary["running"]), (None, LISTED + 1))
        self.assertNotIn("running_jobs", summary)
        self.assertEqual(queue_summary("", "x"), {
            "job": None, "running": 0, "pending": 0, "running_jobs": [],
        })

    async def test_queue_failures_do_not_fail_the_status(self):
        jobs = self.jobs()
        failing = SimpleNamespace(returncode=1, communicate=AsyncMock(
            return_value=(b"", b"squeue: error: Invalid partition"),
        ))
        with patch("asyncio.create_subprocess_exec", return_value=failing):
            self.assertEqual(await jobs.queue("x"), {"error": "squeue: error: Invalid partition"})
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("squeue")):
            self.assertIn("Could not read the queue", (await jobs.queue("x"))["error"])

    async def test_cancelled_jobs_are_terminated(self):
        async def communicate(request):
            await asyncio.Event().wait()

        process = piped(SimpleNamespace(returncode=0, communicate=communicate))
        queue = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=lambda *command, **kwargs: queue if command[0] == "squeue" else process,
        ):
            gateway = create_slurm_server(
                "synthetic", partition="qpu", output=Path("runs"),
                python=sys.executable, workdir=Path.cwd(), slurm_args=[],
            )
            async with Client(gateway) as client:
                run_id = (await client.call_tool("execute_protocol", {
                    "protocol": "synthetic", "parameters": {}, "targets": [0], "wait": 0.1,
                })).structured_content["run_id"]
                stopped = (await client.call_tool(
                    "cancel_run", {"run_id": run_id, "wait": 5},
                )).structured_content
                self.assertEqual(stopped["status"], "cancelled")
        self.assertEqual(process.terminated, 1)

    async def test_cancellation_terminates_the_worker(self):
        async def communicate(request):
            await asyncio.Event().wait()

        process = piped(SimpleNamespace(returncode=0, communicate=communicate))
        with patch("asyncio.create_subprocess_exec", return_value=process):
            call = asyncio.create_task(self.jobs().call("context", {}))
            await asyncio.sleep(0.05)
            call.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await call
        self.assertEqual(process.terminated, 1)
