"""Cold-process startup and stdio discovery must not wait for Qibocal."""

import asyncio
import subprocess
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from qibocal_mcp.server import create_server


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_discovery_does_not_block_listing_or_cancel_initialization(self):
        started = asyncio.Event()
        release = asyncio.Event()
        closed = asyncio.Event()

        @asynccontextmanager
        async def lifespan(server):
            started.set()
            try:
                await release.wait()
                yield {}
            finally:
                closed.set()

        backend = FastMCP("Delayed hardware", lifespan=lifespan)

        @backend.resource("qibocal://context")
        async def context() -> str:
            return '{"platform": "synthetic"}'

        with patch("qibocal_mcp.server.StdioTransport", return_value=backend) as transport:
            async with Client(create_server("synthetic")) as client:
                await asyncio.wait_for(started.wait(), timeout=2)
                self.assertEqual(len(await client.list_tools()), 4)
                self.assertEqual(len(await client.list_resources()), 1)
                pending = asyncio.create_task(client.call_tool("get_calibration_context", {}))
                await asyncio.sleep(0.05)
                self.assertFalse(pending.done())
                self.assertTrue(await client.ping())
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                self.assertFalse(closed.is_set())
                release.set()
                result = await client.call_tool("get_calibration_context", {})
                self.assertEqual(result.content[0].text, '{"platform": "synthetic"}')
            self.assertTrue(closed.is_set())
            transport.assert_called_once()

    async def test_initialization_failure_is_logged_and_not_retried(self):
        @asynccontextmanager
        async def lifespan(server):
            raise RuntimeError("Instrument unavailable")
            yield {}

        backend = FastMCP("Unavailable hardware", lifespan=lifespan)
        with (
            patch("qibocal_mcp.server.StdioTransport", return_value=backend) as transport,
            self.assertLogs("qibocal_mcp.server", level="ERROR") as logs,
        ):
            async with Client(create_server("synthetic")) as client:
                self.assertEqual(len(await client.list_tools()), 4)
                for _ in range(2):
                    with self.assertRaisesRegex(ToolError, "Instrument unavailable"):
                        await client.call_tool("get_calibration_context", {})
            transport.assert_called_once()
            self.assertIn("Qibocal initialization failed", logs.output[0])

    async def test_shutdown_cancels_pending_initialization(self):
        started = asyncio.Event()
        closed = asyncio.Event()

        @asynccontextmanager
        async def lifespan(server):
            started.set()
            try:
                await asyncio.Event().wait()
                yield {}
            finally:
                closed.set()

        backend = FastMCP("Pending hardware", lifespan=lifespan)
        with patch("qibocal_mcp.server.StdioTransport", return_value=backend):
            async with Client(create_server("synthetic")):
                await asyncio.wait_for(started.wait(), timeout=2)
            self.assertTrue(closed.is_set())

    def test_entry_points_do_not_import_calibration_stack(self):
        result = subprocess.run(
            [sys.executable, "-c", """
import sys
from pathlib import Path
from qibocal_mcp.server import create_server
from qibocal_mcp.slurm import create_slurm_server
create_server("dummy")
create_slurm_server(
    "dummy", partition="qpu", output=Path("runs"), python=sys.executable,
    workdir=Path("."), slurm_args=[],
)
assert "qibocal" not in sys.modules
assert "qibolab" not in sys.modules
"""],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_stdio_handshake_and_listing_precede_qibocal_import(self):
        runtime_script = """
import importlib.abc
import importlib.machinery
import sys
import time
from pathlib import Path

release = Path(sys.argv[1])
started = Path(sys.argv[2])

class DelayedCalibrationLoader(importlib.abc.Loader):
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        print("Background calibration import started", flush=True)
        started.touch()
        while not release.exists():
            time.sleep(0.01)
        self.loader.exec_module(module)

class DelayedCalibrationImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "qibocal":
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            spec.loader = DelayedCalibrationLoader(spec.loader)
            return spec
        return None

sys.meta_path.insert(0, DelayedCalibrationImport())
from qibocal_mcp import runtime
from qibolab import create_platform
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration

def create_synthetic(name):
    return CalibrationPlatform(
        **vars(create_platform("dummy")),
        calibration=Calibration(single_qubits={0: QubitCalibration()}),
    )

runtime.create_calibration_platform = create_synthetic
sys.argv = [sys.argv[0], *sys.argv[3:]]
runtime.main()
"""
        script = """
import sys
from qibocal_mcp import server

original_transport = server.StdioTransport

def delayed_transport(command, args, **kwargs):
    return original_transport(
        command, ["-c", sys.argv[4], sys.argv[1], sys.argv[2], *args[2:]],
        **kwargs,
    )

server.StdioTransport = delayed_transport
server.main(["--no-config", "--platform", "dummy", "--output", sys.argv[3]])
"""
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryFile(mode="w+") as diagnostics,
        ):
            root = Path(directory)
            release = root / "release"
            started = root / "started"
            transport = StdioTransport(
                command=sys.executable,
                args=[
                    "-c", script, str(release), str(started), str(root / "runs"),
                    runtime_script,
                ],
                keep_alive=False, log_file=diagnostics,
            )
            try:
                async with Client(transport, init_timeout=5, timeout=60) as client:
                    try:
                        self.assertEqual(len(await asyncio.wait_for(
                            client.list_tools(), timeout=2,
                        )), 4)
                        self.assertEqual(len(await client.list_resources()), 1)
                        async with asyncio.timeout(5):
                            while not started.exists():
                                await asyncio.sleep(0.01)
                        self.assertFalse(release.exists())
                        self.assertFalse((root / "runs").exists())
                    finally:
                        release.touch()
                    try:
                        result = await client.call_tool("get_calibration_context", {})
                    except ToolError as error:
                        diagnostics.seek(0)
                        error.add_note(diagnostics.read())
                        raise
                    self.assertIn('"platform": "dummy"', result.content[0].text)
            finally:
                release.touch()
            diagnostics.seek(0)
            self.assertIn("Background calibration import started", diagnostics.read())
