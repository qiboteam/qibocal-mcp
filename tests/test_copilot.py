"""Native profile rendering and launch behavior without models or hardware."""

import base64
import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, ImageContent, TextContent, TextResourceContents
from pydantic_ai.exceptions import UserError
from qibocal_mcp import config, copilot, copilot_server
from qibocal_mcp.agent import transport_for
from qibocal_mcp.calibration import INSTRUCTIONS, NAME, TOOLS, REPORT_FIGURE_MIME_TYPE
from qibocal_mcp.deployment import DeploymentSettings, launch_for


def profile_for(settings):
    files = copilot.plugin_files(settings)
    return json.loads(files[f"agents/{NAME}.agent.md"].split("---\n")[1])


def relay_launch(server):
    return json.loads(base64.b64decode(server["args"][-1]))


class CopilotProfileTests(unittest.TestCase):
    def test_profile_uses_shared_policy_and_restricted_tools_without_model(self):
        settings = DeploymentSettings(platform="qpu", timeout=10)
        files = copilot.plugin_files(settings)
        self.assertEqual(files, copilot.plugin_files(settings))
        text = files[f"agents/{NAME}.agent.md"]
        self.assertIn(INSTRUCTIONS, text)
        self.assertEqual(text.count(INSTRUCTIONS), 1)
        profile = profile_for(settings)
        self.assertNotIn("model", profile)
        self.assertEqual(profile["tools"], [*(f"qibocal/{name}" for name in TOOLS), "ask_user", "view"])
        server = profile["mcp-servers"]["qibocal"]
        self.assertEqual(server["timeout"], 10000)
        self.assertEqual(server["tools"], list(TOOLS))
        self.assertIn("start_session", server["tools"])
        self.assertIn("qibocal/start_session", profile["tools"])
        self.assertIn("get_calibration_context", text)
        self.assertIn("image pixels must actually be available", text)
        self.assertEqual(copilot.PROFILE_ID, "qibocal-calibration:qibocal-calibration")

    def test_both_adapters_share_commands_and_directory_for_all_deployments(self):
        variants = [
            {},
            {"partition": "qpu", "workdir": "relative/project"},
            {"server_python": "/custom/python", "workdir": "/project with spaces"},
            {"venv": "lab", "platform_repo": ["git@host:platforms"], "dependency": ["driver"]},
            {"ssh": "access-node"},
            {"ssh": "access-node", "server_python": "/shared/python",
             "partition": "qpu", "workdir": "/shared/project ${literal}"},
        ]
        with patch.dict(os.environ, {"QIBOLAB_PLATFORMS": "/platforms", "SSH_AUTH_SOCK": "/socket"}):
            for options in variants:
                with self.subTest(options=options):
                    settings = DeploymentSettings(platform="qpu", **options)
                    transport = transport_for(settings)
                    server = profile_for(settings)["mcp-servers"]["qibocal"]
                    relay = relay_launch(server)
                    self.assertEqual(relay["command"], transport.command)
                    self.assertEqual(relay["args"], transport.args)
                    self.assertEqual(relay["cwd"], transport.cwd)
                    self.assertIn("--no-config", " ".join(relay["args"]))

    def test_environment_values_and_provider_credentials_are_not_serialized(self):
        with patch.dict(os.environ, {
            "LAB_DRIVER_KEY": "fixture-driver-value",
            "GITHUB_COPILOT_API_KEY": "fixture-provider-value",
            "OPENAI_API_KEY": "fixture-openai-value",
            "GH_TOKEN": "fixture-gh-value",
            "COPILOT_MCP_DRIVER": "fixture-mcp-driver",
        }, clear=True):
            files = copilot.plugin_files(DeploymentSettings(platform="qpu"))
            profile = json.loads(files[f"agents/{NAME}.agent.md"].split("---\n")[1])
        serialized = "".join(files.values())
        for value in ("fixture-driver-value", "fixture-provider-value", "fixture-openai-value", "fixture-gh-value", "fixture-mcp-driver"):
            self.assertNotIn(value, serialized)
        self.assertEqual(
            profile["mcp-servers"]["qibocal"]["env"],
            {"LAB_DRIVER_KEY": "${LAB_DRIVER_KEY}", "COPILOT_MCP_DRIVER": "${COPILOT_MCP_DRIVER}"},
        )

    def test_native_timeout_rejects_node_timer_overflow_and_submillisecond_values(self):
        for timeout in (1e308, 0.0001):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(copilot.CopilotError, "timeout"):
                    copilot.plugin_files(DeploymentSettings(platform="qpu", timeout=timeout))

    def test_client_rendering_reaches_remote_bootstrap_and_rejects_pydantic_adapter(self):
        settings = DeploymentSettings(
            platform="qpu", ssh="qrc", partition="qpu", report_rendering="client",
        )
        relay = relay_launch(profile_for(settings)["mcp-servers"]["qibocal"])
        self.assertIn("--report-rendering client", relay["args"][-1])
        with self.assertRaisesRegex(UserError, "requires qibocal-copilot"):
            transport_for(settings)

    def test_export_does_not_require_copilot_or_open_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "new plugin"
            with patch("qibocal_mcp.copilot.copilot_executable") as executable:
                result = copilot.export_plugin(target, DeploymentSettings(platform="qpu"))
            executable.assert_not_called()
            self.assertEqual(result, target)
            self.assertTrue((target / "plugin.json").is_file())
            original = (target / f"agents/{NAME}.agent.md").read_text()
            with self.assertRaises(FileExistsError):
                copilot.export_plugin(target, DeploymentSettings(platform="other"))
            self.assertEqual((target / f"agents/{NAME}.agent.md").read_text(), original)

    def test_persistent_plugin_default_and_empty_xdg_use_agent_data_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": ""}),
                patch.object(Path, "home", return_value=home),
            ):
                directory = copilot.persistent_plugin(DeploymentSettings(platform="qpu"))
            self.assertTrue(directory.is_relative_to(home / ".local/share/qibocal/agent/plugins"))

    def test_copilot_import_does_not_require_pydantic_ai(self):
        program = """
import sys
sys.modules['pydantic_ai'] = None
from qibocal_mcp import copilot
from qibocal_mcp.deployment import DeploymentSettings
assert copilot.plugin_files(DeploymentSettings(platform='qpu'))
"""
        subprocess.run([sys.executable, "-c", program], check=True, capture_output=True)


class CopilotCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        environment = patch.dict(os.environ, {"QIBOCAL_MCP_CONFIG_DIR": str(self.directory)})
        environment.start()
        self.addCleanup(environment.stop)
        data = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.directory / "data")})
        data.start()
        self.addCleanup(data.stop)

    def assert_cli_error(self, args, pattern):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            copilot.main(args)
        self.assertEqual(error.exception.code, 2)
        self.assertRegex(stderr.getvalue(), pattern)

    def test_shared_settings_do_not_require_or_use_model(self):
        config.save_config({"agent": {"platform": "qpu", "ssh": "node", "timeout": 90}})
        with patch("qibocal_mcp.copilot.launch_copilot", return_value=0) as launch:
            copilot.main(["--output", "explicit", "--", "--model", "native-model"])
        settings, objective, native = launch.call_args.args
        self.assertEqual(settings.platform, "qpu")
        self.assertEqual(settings.ssh, "node")
        self.assertEqual(settings.output, "explicit")
        self.assertEqual(settings.timeout, 90)
        self.assertIsNone(objective)
        self.assertEqual(native, ["--model", "native-model"])
        config.save_config({"agent": {
            "platform": "qpu", "model": "openwebui:unused", "model_base_url": "https://unused/api",
        }})
        with patch("qibocal_mcp.copilot.launch_copilot", return_value=0) as launch:
            copilot.main(["inspect only"])
        self.assertEqual(launch.call_args.args[1], "inspect only")
        self.assertFalse(hasattr(launch.call_args.args[0], "model"))

    def test_named_launch_uses_saved_native_options_and_objective(self):
        config.save_config({"agents": {"remote": {
            "platform": "qpu", "ssh": "node", "objective": "Inspect only",
            "copilot_options": ["--model", "native-model"],
        }}})
        with patch.object(copilot, "launch_copilot", return_value=0) as launch:
            copilot.main(["remote"])
            settings, objective, native = launch.call_args.args
            self.assertEqual(settings.ssh, "node")
            self.assertEqual(objective, "Inspect only")
            self.assertEqual(native, ["--model", "native-model"])
            copilot.main(["remote", "--output", "explicit", "Explicit inspect", "--", "--model", "override"])
            self.assertEqual(launch.call_args.args[1], "Explicit inspect")
            self.assertEqual(
                launch.call_args.args[2],
                ["--model", "native-model", "--model", "override"],
            )

    def test_print_options_creates_reusable_immutable_plugin_without_launching(self):
        config.save_config({"agents": {"remote": {
            "platform": "qpu", "ssh": "node", "copilot_options": ["--model", "native"],
        }}})
        with (
            patch.object(copilot, "copilot_executable") as executable,
            patch.object(copilot, "launch_copilot") as launch,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            copilot.main(["remote", "--print-options"])
        executable.assert_not_called()
        launch.assert_not_called()
        options = shlex.split(output.getvalue())
        self.assertNotEqual(options[0], "copilot")
        directory = Path(options[options.index("--plugin-dir") + 1])
        self.assertTrue(directory.is_relative_to(self.directory / "data/qibocal/agent/plugins"))
        self.assertTrue((directory / "plugin.json").is_file())
        self.assertEqual(options, copilot.command_for(directory, options=["--model", "native"])[1:])
        self.assertIn("--excluded-tools=skill,sql", options)
        self.assertIn("--disable-builtin-mcps", options)
        profile = json.loads((directory / f"agents/{NAME}.agent.md").read_text().split("---\n")[1])
        self.assertEqual(relay_launch(profile["mcp-servers"]["qibocal"])["command"], "ssh")
        with contextlib.redirect_stdout(io.StringIO()) as repeated:
            copilot.main(["remote", "--print-options"])
        self.assertEqual(repeated.getvalue(), output.getvalue())
        original = (directory / "plugin.json").read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as changed:
            copilot.main(["remote", "--output", "changed", "--print-options"])
        self.assertNotEqual(changed.getvalue(), output.getvalue())
        self.assertEqual((directory / "plugin.json").read_bytes(), original)
        (directory / "plugin.json").write_text("modified")
        self.assert_cli_error(["remote", "--print-options"], "was modified")
        self.assertEqual((directory / "plugin.json").read_text(), "modified")

    def test_print_options_preserves_spaces_and_newlines(self):
        objective = "Inspect only;\nstop on errors"
        config.save_config({"agents": {"local": {
            "platform": "qpu", "objective": objective,
            "copilot_options": ["--model", "native"],
        }}})
        with (
            patch.dict(os.environ, {"XDG_DATA_HOME": str(self.directory / "data with spaces")}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            copilot.main(["local", "--print-options0"])
        options = output.getvalue().split("\0")[:-1]
        self.assertEqual(options[-4:], ["--interactive", objective, "--model", "native"])
        self.assertIn("data with spaces", options[1])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            copilot.main(["local", "--print-options"])
        self.assertEqual(shlex.split(output.getvalue())[-4:], options[-4:])

    def test_native_shell_arrays_preserve_paths_objectives_and_tool_restrictions(self):
        objective = "Inspect only;\nstop on errors"
        config.save_config({"agents": {"local": {
            "platform": "qpu", "objective": objective,
            "copilot_options": ["--model", "native"],
        }}})
        command = shlex.join([
            sys.executable, "-m", "qibocal_mcp.copilot", "local", "--print-options0",
        ])
        script = (
            "copilot_args=(); "
            "while IFS= read -r -d '' arg; do copilot_args+=(\"$arg\"); done "
            f"< <({command}); "
            "printf '%s\\0' \"${copilot_args[@]}\""
        )
        for shell in ("bash", "zsh"):
            executable = shutil.which(shell)
            if executable is None:
                continue
            with (
                self.subTest(shell=shell),
                patch.dict(os.environ, {"XDG_DATA_HOME": str(self.directory / "data with spaces")}),
            ):
                result = subprocess.run(
                    [executable, "-c", script], capture_output=True, text=True, check=True,
                )
            options = result.stdout.split("\0")[:-1]
            self.assertEqual(options[-4:], ["--interactive", objective, "--model", "native"])
            self.assertIn("data with spaces", options[1])
            self.assertTrue(Path(options[1]).is_dir())
            self.assertIn("--disable-builtin-mcps", options)
            self.assertIn("--excluded-tools=skill,sql", options)

    def test_simple_bash_command_substitution_supplies_native_arguments(self):
        config.save_config({"agents": {"local": {"platform": "qpu"}}})
        command = shlex.join([
            sys.executable, "-m", "qibocal_mcp.copilot", "local", "--print-options",
        ])
        result = subprocess.run(
            ["bash", "-c", f"printf '%s\\0' $({command})"],
            capture_output=True, text=True, check=True,
        )
        options = result.stdout.split("\0")[:-1]
        self.assertEqual(len(options), 6)
        self.assertEqual(options[0], "--plugin-dir")
        self.assertTrue(Path(options[1]).is_dir())
        self.assertEqual(options[-2:], [
            "--disable-builtin-mcps", "--excluded-tools=skill,sql",
        ])

    def test_print_options_rejects_configured_permission_overrides(self):
        for forbidden in (
            "--agent=other", "--available-tools=all", "--excluded-tools=",
            "--plugin-dir=other", "-pacquire",
            "--disable-builtin-mcps=false",
        ):
            with self.subTest(forbidden=forbidden):
                config.save_config({"agents": {"local": {
                    "platform": "qpu", "copilot_options": [forbidden],
                }}})
                self.assert_cli_error(["local", "--print-options"], "cannot replace")
        self.assertFalse((self.directory / "data").exists())

    def test_print_options_and_export_are_mutually_exclusive(self):
        self.assert_cli_error(
            ["--print-options", "--export-plugin", str(self.directory / "plugin")],
            "not allowed",
        )

    def test_named_export_keeps_saved_objective_and_options_in_launch_command(self):
        config.save_config({"agents": {"local": {
            "platform": "qpu", "objective": "Inspect only",
            "copilot_options": ["--model", "native"],
        }}})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            copilot.main(["local", "--export-plugin", str(self.directory / "plugin")])
        self.assertEqual(
            shlex.split(output.getvalue())[-4:],
            ["--interactive", "Inspect only", "--model", "native"],
        )
        self.assert_cli_error(
            ["local", "Explicit objective", "--export-plugin", str(self.directory / "other")],
            "cannot be combined",
        )
        self.assert_cli_error(
            ["local", "--export-plugin", str(self.directory / "other"), "--", "--model", "explicit"],
            "cannot be combined",
        )

    def test_no_config_and_validation_are_preserved(self):
        (self.directory / "agent.json").write_text("{broken")
        with patch("qibocal_mcp.copilot.launch_copilot", return_value=0):
            copilot.main(["--no-config", "--platform", "explicit"])
        self.assert_cli_error(["--platform", "qpu"], "Could not read")
        self.assert_cli_error(["--no-config"], "--platform is required")
        self.assert_cli_error(
            ["--no-config", "--platform", "qpu", "--slurm-arg=--account=q"], "requires --partition",
        )
        self.assert_cli_error(
            ["--no-config", "--platform", "qpu", "--venv", "lab", "--server-python", "/python"],
            "cannot be combined",
        )

    def test_export_prints_direct_launch_and_rejects_conflicting_options(self):
        destination = self.directory / "plugin"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            copilot.main(["--no-config", "--platform", "qpu", "--export-plugin", str(destination)])
        self.assertIn("--agent qibocal-calibration", output.getvalue())
        self.assertIn(copilot.PROFILE_ID, output.getvalue())
        self.assertIn("--excluded-tools=skill,sql", output.getvalue())
        self.assertTrue(destination.exists())
        base = ["--no-config", "--platform", "qpu"]
        self.assert_cli_error(base + ["--export-plugin", str(destination), "objective"], "cannot be combined")
        self.assert_cli_error(base + ["--", "--agent=other"], "cannot replace")
        self.assert_cli_error(base + ["--", "-p", "acquire"], "cannot replace")
        self.assert_cli_error(base + ["--", "-pacquire"], "cannot replace")
        self.assert_cli_error(base + ["--", "--excluded-tools="], "cannot replace")

    def test_missing_or_incompatible_copilot_is_actionable(self):
        with patch("qibocal_mcp.copilot.shutil.which", return_value=None):
            with self.assertRaisesRegex(copilot.CopilotError, "Install"):
                copilot.copilot_executable()
        with (
            patch("qibocal_mcp.copilot.shutil.which", return_value="/copilot"),
            patch("qibocal_mcp.copilot.subprocess.run", return_value=Mock(stdout="--agent --interactive")),
        ):
            with self.assertRaisesRegex(copilot.CopilotError, "--plugin-dir"):
                copilot.copilot_executable()

    def test_launch_inherits_terminal_keeps_artifacts_until_exit_and_cleans(self):
        paths = []

        def start(command):
            plugin = Path(command[command.index("--plugin-dir") + 1])
            paths.append(plugin)
            self.assertTrue((plugin / "plugin.json").is_file())
            self.assertIn("--interactive", command)
            self.assertEqual(command[command.index("--agent") + 1], copilot.PROFILE_ID)
            self.assertEqual(command[-2:], ["--model", "native-model"])
            self.assertNotIn("--allow-all", command)
            self.assertIn("--excluded-tools=skill,sql", command)
            process = Mock()
            process.wait.side_effect = lambda: 7 if plugin.exists() else self.fail("cleaned too early")
            return process

        with (
            patch("qibocal_mcp.copilot.copilot_executable", return_value="/copilot"),
            patch("qibocal_mcp.copilot.subprocess.Popen", side_effect=start) as create,
        ):
            result = copilot.launch_copilot(DeploymentSettings(platform="qpu"), "inspect", ["--model", "native-model"])
        self.assertEqual(result, 7)
        self.assertEqual(create.call_args.kwargs, {})
        self.assertFalse(paths[0].exists())

    def test_interruption_terminates_child_and_cleans(self):
        process = Mock()
        process.wait.side_effect = [KeyboardInterrupt, 0]
        process.poll.return_value = None
        with (
            patch("qibocal_mcp.copilot.copilot_executable", return_value="/copilot"),
            patch("qibocal_mcp.copilot.subprocess.Popen", return_value=process) as create,
        ):
            result = copilot.launch_copilot(DeploymentSettings(platform="qpu"))
        self.assertEqual(result, 130)
        process.terminate.assert_called_once()
        plugin = Path(create.call_args.args[0][2])
        self.assertFalse(plugin.exists())


class CopilotRelayTests(unittest.TestCase):
    def test_encoded_arguments_and_directory_are_literal(self):
        settings = DeploymentSettings(platform="qpu", workdir="project ${literal}")
        with patch.dict(os.environ, {"LAB_DRIVER": "a ${literal} value"}, clear=True):
            launch = launch_for(settings)
            server = copilot.server_config(launch, settings.timeout)
            decoded, timeout = copilot_server.decode_launch(server["args"][-1])
        self.assertEqual(decoded, launch)
        self.assertEqual(decoded.cwd, "project ${literal}")
        self.assertEqual(decoded.env, {"LAB_DRIVER": "a ${literal} value"})
        self.assertEqual(timeout, settings.timeout)

    def test_missing_environment_or_invalid_payload_is_an_error(self):
        with patch.dict(os.environ, {"LAB_DRIVER": "value"}, clear=True):
            server = copilot.server_config(launch_for(DeploymentSettings(platform="qpu")), 10)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "LAB_DRIVER"):
                copilot_server.decode_launch(server["args"][-1])
        with patch.dict(os.environ, {"LAB_DRIVER": "${LAB_DRIVER}"}, clear=True):
            with self.assertRaisesRegex(ValueError, "LAB_DRIVER"):
                copilot_server.decode_launch(server["args"][-1])
        invalid = base64.b64encode(b'{"command": 1}').decode()
        with self.assertRaisesRegex(ValueError, "Invalid"):
            copilot_server.decode_launch(invalid)


class CopilotReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_rendering_failure_is_not_a_successful_report(self):
        upstream = FastMCP("Client-rendered report")

        @upstream.tool
        def execute_protocol():
            return ToolResult(content=[EmbeddedResource(
                type="resource", resource=TextResourceContents(
                    uri="qibocal://reports/synthetic/0/0",
                    mimeType=REPORT_FIGURE_MIME_TYPE,
                    text='{"data": [], "layout": {}}',
                ),
            )])

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("plotly.graph_objects.Figure.to_image", side_effect=RuntimeError("Chrome unavailable")),
        ):
            async with Client(upstream) as backend:
                async with Client(copilot_server.report_proxy(backend, Path(directory))) as client:
                    with self.assertRaisesRegex(ToolError, "Chrome unavailable"):
                        await client.call_tool("execute_protocol")
                    self.assertFalse(list(Path(directory).rglob("*.png")))

    async def test_proxy_rejects_a_disconnected_upstream(self):
        with self.assertRaisesRegex(ValueError, "connected upstream"):
            copilot_server.report_proxy(Client(FastMCP("Disconnected")), Path("/unused"))

    async def test_proxy_preserves_schemas_state_metadata_and_stages_received_images(self):
        upstream = FastMCP("Synthetic upstream")
        calls = []
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
        )

        @upstream.tool
        async def execute_protocol(protocol: str, parameters: dict, targets: list[int]):
            calls.append(protocol)
            return ToolResult(
                content=[
                    TextContent(type="text", text='{"run_id":"same-session"}'),
                    ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png"),
                ],
                structured_content={"run_id": "same-session", "folder": "/remote/shared/run"},
            )

        @upstream.tool
        async def update_platform(run_id: str):
            if run_id != "same-session" or calls != ["synthetic"]:
                raise ValueError("Wrong upstream session")
            return {"updated": True}

        with tempfile.TemporaryDirectory() as directory:
            async with Client(upstream) as backend:
                schemas = await backend.list_tools()
                proxy = copilot_server.report_proxy(backend, Path(directory))
                async with Client(proxy) as client:
                    proxied = await client.list_tools()
                    self.assertEqual(
                        {tool.name: tool.inputSchema for tool in schemas},
                        {tool.name: tool.inputSchema for tool in proxied},
                    )
                    result = await client.call_tool(
                        "execute_protocol", {"protocol": "synthetic", "parameters": {}, "targets": [0]},
                    )
                    metadata = result.structured_content
                    self.assertEqual(metadata["run_id"], "same-session")
                    self.assertEqual(metadata["folder"], "/remote/shared/run")
                    self.assertTrue(metadata["inspection_required"])
                    path = Path(metadata["copilot_report_images"][0])
                    self.assertEqual(path.read_bytes(), png)
                    self.assertTrue(path.is_relative_to(directory))
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                    self.assertTrue(any(isinstance(item, ImageContent) for item in result.content))
                    self.assertTrue(any(
                        isinstance(item, TextContent) and "copilot_report_images" in item.text
                        for item in result.content
                    ))
                    await client.call_tool("update_platform", {"run_id": "same-session"})
                    with self.assertRaises(ToolError):
                        await client.call_tool("update_platform", {"run_id": "wrong"})
                self.assertTrue(backend.is_connected())

    async def test_staging_rejects_invalid_image_without_success_fallback(self):
        upstream = FastMCP("Broken report")

        @upstream.tool
        def execute_protocol():
            return ToolResult(content=[ImageContent(type="image", data="invalid!", mimeType="image/png")])

        with tempfile.TemporaryDirectory() as directory:
            async with Client(upstream) as backend:
                proxy = copilot_server.report_proxy(backend, Path(directory))
                async with Client(proxy) as client:
                    with self.assertRaises(ToolError):
                        await client.call_tool("execute_protocol")
