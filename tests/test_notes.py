"""Comment histories across direct servers, relays and fresh queued workers."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import ValidationError
from qibolab import create_platform
from qibocal import Note
from qibocal.auto.notes import dump_notes, load_notes
from qibocal.auto.operation import Protocol
from qibocal.auto.output import Output
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration

from qibocal_mcp.calibration import INSTRUCTIONS, TOOLS
from qibocal_mcp.environment import locked
from qibocal_mcp.notes import append_note, read_notes
from qibocal_mcp.runtime import create_server
from qibocal_mcp.server import create_server as create_frontend
from qibocal_mcp.sessions import PlatformState
from qibocal_mcp.slurm import create_slurm_server
from qibocal_mcp.worker import run
from test_server import acquire, fit, update


class PersistenceTests(unittest.TestCase):
    def test_parallel_writers_share_the_report_server_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)

            def write(index):
                note = Note(content=f"Finding {index}")
                if index % 2:
                    # Simulate qibocal-report's independent load/append/replace.
                    with locked(folder / ".notes.lock"):
                        notes = [*load_notes(folder), note]
                        with tempfile.TemporaryDirectory(dir=folder) as temporary:
                            dump_notes(notes, Path(temporary))
                            (Path(temporary) / "notes.json").replace(folder / "notes.json")
                else:
                    append_note(folder, note)

            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(write, range(32)))
            notes = read_notes(folder)
            self.assertEqual(len(notes), 32)
            self.assertEqual({note.content for note in notes}, {f"Finding {i}" for i in range(32)})

    def test_corrupt_histories_and_failed_saves_are_not_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / "notes.json"
            path.write_text("not JSON")
            with self.assertRaises(ValidationError):
                append_note(folder, Note(content="Do not overwrite"))
            self.assertEqual(path.read_text(), "not JSON")
            dump_notes([Note(content="Original")], folder)
            original = path.read_bytes()
            with patch("qibocal_mcp.notes.dump_notes", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    append_note(folder, Note(content="Not saved"))
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(folder.glob(".notes-*")))

    def test_notes_and_lock_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            destination = folder / "other.json"
            dump_notes([], folder)
            (folder / "notes.json").rename(destination)
            for name in ("notes.json", ".notes.lock"):
                link = folder / name
                link.unlink(missing_ok=True)
                link.symlink_to(destination)
                with self.assertRaisesRegex(ValueError, "symbolic links"):
                    append_note(folder, Note(content="Not saved"))
                link.unlink()
            self.assertEqual(destination.read_text(), "[]")

    def test_shared_agent_policy_records_plans_findings_and_followups(self):
        self.assertIn("append_note", TOOLS)
        self.assertIn("get_notes", TOOLS)
        for instruction in (
            "before the first experiment", "After inspecting each run",
            "Append follow-up notes", "final summary", "new session plan",
        ):
            self.assertIn(instruction, INSTRUCTIONS)


class NotesTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode):
        platforms = []

        def create(_):
            platform = CalibrationPlatform(
                **vars(create_platform("dummy")),
                calibration=Calibration(single_qubits={0: QubitCalibration()}),
            )
            PlatformState.capture(platform).patched({
                "/parameters/settings/nshots": 100 + len(platforms),
            }).apply(platform)
            platforms.append(platform)
            return platform

        async def start(*command, **kwargs):
            async def communicate(request):
                result = await run(
                    "synthetic",
                    output=Path(command[command.index("--output") + 1]),
                    snapshot=(
                        Path(command[command.index("--snapshot") + 1])
                        if "--snapshot" in command else None
                    ),
                    session_id=command[command.index("--session-id") + 1],
                    request=json.loads(request),
                )
                return result.model_dump_json(by_alias=True).encode(), b""

            return SimpleNamespace(returncode=0, communicate=communicate)

        protocol = Protocol(acquire, fit, update=update)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("qibocal_mcp.runtime.create_calibration_platform", side_effect=create),
            patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": protocol}),
            patch("qibocal.protocols.synthetic", protocol, create=True),
            patch("asyncio.create_subprocess_exec", side_effect=start),
        ):
            output = Path(directory)
            if mode == "queued":
                server = create_slurm_server(
                    "synthetic", output=output, partition="qpu",
                    python="/shared/python", workdir=output, slurm_args=[],
                )
                backend = server
            else:
                backend = create_server("synthetic", output=output)
                server = backend if mode == "direct" else create_frontend("synthetic")
            with patch("qibocal_mcp.server.StdioTransport", return_value=backend):
                async with Client(server) as client:
                    self.assertEqual({tool.name for tool in await client.list_tools()}, set(TOOLS))
                    empty = (await client.call_tool("get_notes", {})).structured_content
                    report_folder = Path(empty["report_folder"])
                    self.assertEqual(empty["notes"], [])
                    self.assertFalse(report_folder.exists())
                    with self.assertRaises(ToolError):
                        await client.call_tool("append_note", {"content": " \n "})
                    with self.assertRaises(ToolError):
                        await client.call_tool("append_note", {"content": "Invalid", "run_id": "missing"})
                    self.assertFalse(report_folder.exists())

                    before = datetime.now(UTC)
                    plan = (await client.call_tool("append_note", {
                        "content": "  Session plan  ", "author": "calibration-agent",
                    })).structured_content
                    self.assertEqual(plan["notes"][0]["content"], "Session plan")
                    self.assertEqual(plan["notes"][0]["author"], "calibration-agent")
                    self.assertLessEqual(before, datetime.fromisoformat(plan["notes"][0]["timestamp"]))
                    self.assertTrue(report_folder.is_dir())
                    baseline = json.loads((report_folder / "platform" / "parameters.json").read_text())
                    await client.call_tool("append_note", {"content": "Plan update"})

                    runs = []
                    for value in (5e9, 6e9):
                        result = await client.call_tool("execute_protocol", {
                            "protocol": "synthetic", "parameters": {"value": value}, "targets": [0],
                        })
                        runs.append(result.structured_content)
                        self.assertEqual(
                            platforms[-1].parameters.settings.nshots, baseline["settings"]["nshots"],
                        )
                    first, second = runs
                    folder = Path(first["folder"])
                    (folder / "notes.json").unlink()
                    legacy = (await client.call_tool("get_notes", {
                        "run_id": first["run_id"],
                    })).structured_content
                    self.assertEqual(legacy["notes"], [])
                    artifacts = {
                        path: path.read_bytes() for path in (
                            report_folder / "meta.json", report_folder / "history.json",
                            folder / "action.yml", folder / "run.json", folder / "data.npz",
                            folder / "results.json",
                        )
                    }
                    for content in ("Initial fit finding", "Follow-up finding"):
                        history = (await client.call_tool("append_note", {
                            "content": content, "run_id": first["run_id"],
                        })).structured_content
                    self.assertEqual([note["content"] for note in history["notes"]], [
                        "Initial fit finding", "Follow-up finding",
                    ])
                    self.assertTrue(all(note["author"] is None for note in history["notes"]))
                    for path, data in artifacts.items():
                        self.assertEqual(path.read_bytes(), data)
                    await client.call_tool("append_note", {
                        "content": "Second iteration", "run_id": second["run_id"],
                    })

                    # Report UI writes must be visible even with cached Completed/Output.
                    for path in (report_folder, folder):
                        dump_notes([*load_notes(path), Note(content="Report UI comment", author="reviewer")], path)
                    stored = {path: (path / "notes.json").read_bytes() for path in (report_folder, folder)}
                    current = (await client.call_tool("get_notes", {"run_id": first["run_id"]})).structured_content
                    self.assertEqual(current["notes"][-1]["author"], "reviewer")
                    await client.call_tool("update_platform", {"run_id": first["run_id"]})
                    await client.call_tool("patch_platform", {"changes": {"/parameters/settings/nshots": 10}})
                    await client.call_tool("execute_protocol", {
                        "protocol": "synthetic", "parameters": {"value": 7e9}, "targets": [0],
                    })
                    for path, data in stored.items():
                        self.assertEqual((path / "notes.json").read_bytes(), data)
                    loaded = Output.load(report_folder)
                    self.assertEqual([note.content for note in loaded.notes], [
                        "Session plan", "Plan update", "Report UI comment",
                    ])
                    self.assertEqual(loaded.history["synthetic"][0].notes, load_notes(folder))
                    self.assertEqual([note.content for note in loaded.history["synthetic"][1].notes], [
                        "Second iteration",
                    ])
                    await client.call_tool("append_note", {
                        "content": "Accepted update recorded", "run_id": first["run_id"],
                    })
                    self.assertEqual(len(load_notes(folder)), 4)
                    notes_file = folder / "notes.json"
                    original = notes_file.read_bytes()
                    notes_file.write_text("not JSON")
                    for tool in ("append_note", "get_notes"):
                        arguments = {"run_id": first["run_id"]}
                        if tool == "append_note":
                            arguments["content"] = "Do not overwrite corruption"
                        with self.assertRaises(ToolError):
                            await client.call_tool(tool, arguments)
                    self.assertEqual(notes_file.read_text(), "not JSON")
                    notes_file.write_bytes(original)
                    await client.call_tool("start_session", {})
                    fresh = (await client.call_tool("get_notes", {})).structured_content
                    self.assertNotEqual(fresh["session_id"], empty["session_id"])
                    self.assertEqual(fresh["notes"], [])
                    for tool in ("append_note", "get_notes"):
                        arguments = {"run_id": first["run_id"]}
                        if tool == "append_note":
                            arguments["content"] = "Expired"
                        with self.assertRaises(ToolError):
                            await client.call_tool(tool, arguments)
                    self.assertEqual(len(load_notes(folder)), 4)

    async def test_direct_notes(self):
        await self.exercise("direct")

    async def test_frontend_notes(self):
        await self.exercise("frontend")

    async def test_queued_notes(self):
        await self.exercise("queued")
