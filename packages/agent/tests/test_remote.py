"""Switch interactive configurations through the proxy, with in-memory servers."""

import contextlib
import io
import os
import tempfile
import unittest
import unittest.mock
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from qibocal_agent import config, remote
from qibocal_agent.cli import main


def fake_server(platform: str, events: list[str]) -> FastMCP:
    """Stand in for qibocal-mcp, recording its lifetime."""

    @asynccontextmanager
    async def lifespan(server):
        events.append(f"open {platform}")
        try:
            yield {}
        finally:
            events.append(f"close {platform}")

    server = FastMCP(f"Fake {platform}", lifespan=lifespan)

    @server.tool
    def whoami() -> str:
        return platform

    @server.prompt
    def calibrate() -> str:
        return f"policy for {platform}"

    return server


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        environment = patch.dict(
            os.environ, {"QIBOCAL_AGENTS_CONFIG_DIR": str(self.directory)}
        )
        environment.start()
        self.addCleanup(environment.stop)
        config.save_config({
            "agents": {
                "default": {"platform": "simulator", "output": "/runs/simulator"},
                "platinum": {"platform": "qw5q_platinum", "output": "/runs/platinum"},
                "tuna": {"platform": "tuna5", "output": "/runs/tuna"},
                "broken": {"output": "/runs/broken"},
            },
        })
        self.events = []
        self.failing = set()

        def transport(command, args, **kwargs):
            platform = args[args.index("--platform") + 1]
            if platform in self.failing:
                raise OSError(f"cannot launch {platform}")
            return fake_server(platform, self.events)

        stdio = patch("qibocal_agent.remote.StdioTransport", side_effect=transport)
        stdio.start()
        self.addCleanup(stdio.stop)

    def proxy(self, name=None, cli=None, path=None, enabled=True):
        """Build the proxy as qibocal-agent mcp does."""
        values = config.resolve("agent", cli or {}, name, config=path, enabled=enabled)
        upstream = remote.Upstream(path, enabled)
        return remote.create_proxy(
            upstream, remote.DeploymentSettings.from_values(values), values["name"],
        )

    async def test_switch_replaces_the_server_and_forwards_its_tools(self):
        async with Client(self.proxy()) as client:
            names = {tool.name for tool in await client.list_tools()}
            self.assertEqual(names, {"whoami", "list_configurations", "switch_configuration"})
            self.assertEqual((await client.call_tool("whoami", {})).data, "simulator")
            prompt = await client.get_prompt("calibrate")
            self.assertEqual(prompt.messages[0].content.text, "policy for simulator")

            listing = (await client.call_tool("list_configurations", {})).structured_content
            self.assertEqual(listing["active"]["name"], "default")
            self.assertEqual(listing["active"]["platform"], "simulator")
            entries = {entry["name"]: entry for entry in listing["configurations"]}
            self.assertEqual(set(entries), {"default", "platinum", "tuna", "broken"})
            self.assertEqual(entries["platinum"]["platform"], "qw5q_platinum")
            self.assertIn("--platform is required", entries["broken"]["error"])

            switched = await client.call_tool("switch_configuration", {"name": "platinum"})
            self.assertEqual(switched.structured_content["platform"], "qw5q_platinum")
            self.assertEqual((await client.call_tool("whoami", {})).data, "qw5q_platinum")
            # The previous server closes before the next one opens.
            self.assertEqual(
                self.events,
                ["open simulator", "close simulator", "open qw5q_platinum"],
            )
            await client.call_tool("switch_configuration", {})
            self.assertEqual((await client.call_tool("whoami", {})).data, "simulator")
        self.assertEqual(self.events[-1], "close simulator")

    async def test_invalid_selection_keeps_the_active_server(self):
        async with Client(self.proxy("tuna")) as client:
            for name in ("unknown", "broken"):
                with self.subTest(name=name), self.assertRaises(ToolError):
                    await client.call_tool("switch_configuration", {"name": name})
            self.assertEqual((await client.call_tool("whoami", {})).data, "tuna5")
        self.assertEqual(self.events, ["open tuna5", "close tuna5"])

    async def test_failed_launches_leave_switching_available(self):
        self.failing = {"simulator", "tuna5"}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            async with Client(self.proxy()) as client:
                names = {tool.name for tool in await client.list_tools()}
                self.assertEqual(names, {"list_configurations", "switch_configuration"})
                listing = (await client.call_tool("list_configurations", {})).structured_content
                self.assertFalse(listing["active"]["connected"])
                self.assertIn("cannot launch simulator", listing["active"]["error"])
                with self.assertRaisesRegex(ToolError, "No configuration is active"):
                    await client.call_tool("switch_configuration", {"name": "tuna"})
                await client.call_tool("switch_configuration", {"name": "platinum"})
                self.assertEqual((await client.call_tool("whoami", {})).data, "qw5q_platinum")
        self.assertIn("cannot launch simulator", stderr.getvalue())

    async def test_cli_overrides_apply_only_to_the_initial_configuration(self):
        async with Client(self.proxy(cli={"platform": "override"})) as client:
            self.assertEqual((await client.call_tool("whoami", {})).data, "override")
            await client.call_tool("switch_configuration", {"name": "tuna"})
            self.assertEqual((await client.call_tool("whoami", {})).data, "tuna5")

    async def test_explicit_file_is_reread_and_no_config_disables_switching(self):
        selected = self.directory / "other.json"
        config.save_config({"agents": {"default": {"platform": "first"}}}, selected)
        async with Client(self.proxy(path=selected)) as client:
            # Entries added during the session are usable without restarting.
            config.save_config({
                "agents": {"default": {"platform": "first"}, "added": {"platform": "later"}},
            }, selected)
            await client.call_tool("switch_configuration", {"name": "added"})
            self.assertEqual((await client.call_tool("whoami", {})).data, "later")
        async with Client(self.proxy(cli={"platform": "adhoc"}, enabled=False)) as client:
            listing = (await client.call_tool("list_configurations", {})).structured_content
            self.assertEqual(listing["configurations"], [])
            with self.assertRaisesRegex(ToolError, "--no-config"):
                await client.call_tool("switch_configuration", {"name": "tuna"})
            self.assertEqual((await client.call_tool("whoami", {})).data, "adhoc")

    async def test_without_a_configuration_nothing_starts_until_one_is_selected(self):
        upstream = remote.Upstream(None, True)
        async with Client(remote.create_proxy(upstream)) as client:
            names = {tool.name for tool in await client.list_tools()}
            self.assertEqual(names, {"list_configurations", "switch_configuration"})
            self.assertEqual(self.events, [], "nothing launched by connecting")
            listing = (await client.call_tool("list_configurations", {})).structured_content
            self.assertIsNone(listing["active"]["name"])
            self.assertFalse(listing["active"]["connected"])
            self.assertEqual(self.events, [], "nor by listing")
            await client.call_tool("switch_configuration", {"name": "tuna"})
            self.assertEqual((await client.call_tool("whoami", {})).data, "tuna5")
        self.assertEqual(self.events, ["open tuna5", "close tuna5"])

    def test_the_command_starts_idle_unless_a_configuration_is_given(self):
        started = []

        def proxy(upstream, initial=None, name=None):
            started.append(name if initial is not None else None)
            return unittest.mock.Mock()

        with patch("qibocal_agent.remote.create_proxy", side_effect=proxy):
            for arguments in (["mcp"], ["mcp", "tuna"], ["mcp", "--platform", "adhoc"]):
                result = CliRunner().invoke(main, arguments)
                self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(started, [None, "tuna", "default"])

    def test_command_line_requires_a_platform_but_no_model(self):
        result = CliRunner().invoke(main, ["--no-config", "mcp"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("--platform is required", result.output)
        with patch.object(FastMCP, "run") as run:
            result = CliRunner().invoke(main, ["--no-config", "mcp", "--platform", "qpu"])
        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(transport="stdio", show_banner=False)
