"""Configuration discovery, persistence and CLI precedence without hardware."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from qibocal_mcp import agent, config, server


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ, {"QIBOCAL_MCP_CONFIG_DIR": str(self.directory)}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.path = self.directory / "qibocal-mcp.json"

    def command(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            config.main(list(args))
        return output.getvalue().strip()

    def assert_cli_error(self, callback, pattern):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            callback()
        self.assertEqual(error.exception.code, 2)
        self.assertRegex(stderr.getvalue(), pattern)

    def test_discovery_custom_xdg_and_home(self):
        self.assertEqual(config.get_config_file(), self.path)
        with patch.dict(
            os.environ,
            {"QIBOCAL_MCP_CONFIG_DIR": "", "XDG_CONFIG_HOME": str(self.directory / "xdg")},
        ):
            self.assertEqual(
                config.get_config_file(), self.directory / "xdg/qibocal/qibocal-mcp.json"
            )
            with (
                patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}),
                patch.object(Path, "home", return_value=self.directory),
            ):
                self.assertEqual(
                    config.get_config_file(),
                    self.directory / ".config/qibocal/qibocal-mcp.json",
                )

    def test_read_commands_do_not_create_files(self):
        self.assertEqual(self.command("path"), str(self.path))
        self.assertEqual(self.command("show"), "{}")
        self.assertEqual(config.load_config(), {})
        self.assertEqual(list(self.directory.iterdir()), [])
        with patch.dict(
            os.environ, {"QIBOCAL_MCP_CONFIG_DIR": str(self.directory / "absent")}
        ):
            self.command("path")
            self.command("show")
        self.assertFalse((self.directory / "absent").exists())

    def test_cli_round_trip_and_sibling_files(self):
        siblings = {"servers.json": "report servers", "auth.json": "report auth"}
        for name, text in siblings.items():
            (self.directory / name).write_text(text)
        self.command("set", "server.platform", "qpu")
        self.command("set", "agent.model", "openai:gpt-4.1")
        self.command("set", "agent.timeout", "7200")
        self.command("set", "agent.slurm_arg", '["--account=quantum"]')
        self.assertEqual(self.command("get", "server.platform"), "qpu")
        self.assertEqual(self.command("get", "agent.timeout"), "7200")
        self.assertEqual(
            json.loads(self.command("get", "agent.slurm_arg")), ["--account=quantum"]
        )
        self.assertEqual(
            json.loads(self.command("show")),
            {
                "server": {"platform": "qpu"},
                "agent": {
                    "model": "openai:gpt-4.1",
                    "timeout": 7200,
                    "slurm_arg": ["--account=quantum"],
                },
            },
        )
        self.command("unset", "server.platform")
        self.assertNotIn("server", config.load_config())
        self.assert_cli_error(
            lambda: self.command("get", "server.platform"), "not configured"
        )
        self.assert_cli_error(
            lambda: self.command("unset", "server.platform"), "not configured"
        )
        for name, text in siblings.items():
            self.assertEqual((self.directory / name).read_text(), text)

    def test_explicit_path_does_not_write_default_file(self):
        selected = self.directory / "other/settings.json"
        self.command("--config", str(selected), "set", "server.platform", "other")
        self.assertEqual(config.load_config(selected)["server"]["platform"], "other")
        self.assertFalse(self.path.exists())
        self.assertEqual(
            self.command("--config", str(selected), "path"), str(selected)
        )
        self.assert_cli_error(
            lambda: self.command("--config", str(self.path), "show"), "not found"
        )

    def test_invalid_files_and_values_are_errors(self):
        invalid = [
            [],
            {"other": {}},
            {"server": []},
            {"server": {"platfrom": "qpu"}},
            {"agent": {"objective": "calibrate"}},
            {"agent": {"api_key": "not-a-key"}},
            {"server": {"platform": 5}},
            {"server": {"platform": ""}},
            {"server": {"platform": None}},
            {"server": {"slurm_arg": "--account=quantum"}},
            {"server": {"slurm_arg": [1]}},
            {"agent": {"timeout": True}},
            {"agent": {"timeout": "10"}},
            {"agent": {"timeout": 0}},
            {"agent": {"timeout": -1}},
            {"agent": {"timeout": float("inf")}},
            {"agent": {"timeout": float("nan")}},
            {"agent": {"timeout": 10**400}},
            {"agent": {"timeout": -(10**400)}},
        ]
        for data in invalid:
            with self.subTest(data=data):
                self.path.write_text(json.dumps(data))
                with self.assertRaises(config.ConfigError):
                    config.load_config()
        self.path.write_text("{broken")
        with self.assertRaisesRegex(config.ConfigError, "Could not read"):
            config.load_config()
        original = self.path.read_bytes()
        self.assert_cli_error(
            lambda: self.command("set", "server.platform", "qpu"), "Could not read"
        )
        self.assertEqual(self.path.read_bytes(), original)
        self.path.unlink()
        for key, value in (
            ("agent.timeout", "abc"),
            ("agent.timeout", "true"),
            ("agent.slurm_arg", '"not an array"'),
            ("server.unknown", "value"),
            ("server.platform", ""),
        ):
            with self.subTest(key=key, value=value):
                self.assert_cli_error(
                    lambda: self.command("set", key, value), "error:"
                )
        self.assertFalse(self.path.exists())

    def test_failed_atomic_write_preserves_original_and_cleans_temporary(self):
        config.save_config({"server": {"platform": "original"}})
        with patch("qibocal_mcp.config.os.replace", side_effect=OSError("write denied")):
            with self.assertRaisesRegex(config.ConfigError, "write denied"):
                config.save_config({"server": {"platform": "replacement"}})
        self.assertEqual(config.load_config()["server"]["platform"], "original")
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_invalid_agent_section_also_blocks_server(self):
        self.path.write_text('{"server": {"platform": "qpu"}, "agent": {"timeout": 0}}')
        with patch("qibocal_mcp.server.create_server") as create:
            self.assert_cli_error(lambda: server.main([]), "agent.timeout")
        create.assert_not_called()

    def test_server_file_defaults_and_cli_override(self):
        config.save_config(
            {"server": {"platform": "configured", "output": "configured-runs"}}
        )
        instance = Mock()
        with patch("qibocal_mcp.server.create_server", return_value=instance) as create:
            server.main([])
            create.assert_called_with("configured", output=Path("configured-runs"))
            instance.run.assert_called_with(transport="stdio", show_banner=False)
            server.main(["--platform", "explicit", "--output", "explicit-runs"])
            create.assert_called_with("explicit", output=Path("explicit-runs"))

    def test_server_queue_settings_and_platforms_environment(self):
        config.save_config(
            {
                "server": {
                    "platform": "qpu",
                    "partition": "quantum",
                    "workdir": "/shared/project",
                    "worker_python": "/shared/venv/bin/python",
                    "platforms": "/shared/platforms",
                    "slurm_arg": ["--account=configured"],
                }
            }
        )
        with (
            patch.dict(os.environ, {"QIBOLAB_PLATFORMS": "old"}),
            patch("qibocal_mcp.slurm.create_slurm_server") as create,
        ):
            server.main(["--slurm-arg=--account=explicit", "--slurm-arg=--time=00:10:00"])
            create.assert_called_once_with(
                "qpu", output=Path("runs"), partition="quantum",
                python="/shared/venv/bin/python", workdir=Path("/shared/project"),
                slurm_args=["--account=explicit", "--time=00:10:00"],
            )
            self.assertEqual(os.environ["QIBOLAB_PLATFORMS"], "/shared/platforms")

    def test_server_no_config_and_builtin_defaults(self):
        self.path.write_text("{invalid")
        with (
            patch.dict(os.environ, {"QIBOLAB_PLATFORMS": "/existing/platforms"}),
            patch("qibocal_mcp.server.create_server") as create,
        ):
            server.main(["--no-config", "--platform", "explicit"])
            create.assert_called_with("explicit", output=Path("runs"))
            self.assertEqual(os.environ["QIBOLAB_PLATFORMS"], "/existing/platforms")
        self.assert_cli_error(
            lambda: server.main(["--no-config"]), "--platform is required"
        )
        self.assert_cli_error(
            lambda: server.main(["--no-config", "--config", str(self.path)]),
            "not allowed",
        )

    def test_runtime_explicit_config_is_required_and_replaces_discovery(self):
        config.save_config({"server": {"platform": "discovered"}})
        selected = self.directory / "other.json"
        self.assert_cli_error(
            lambda: server.main(["--config", str(selected), "--platform", "explicit"]),
            "not found",
        )
        config.save_config({"server": {"platform": "selected"}}, selected)
        with patch("qibocal_mcp.server.create_server") as create:
            server.main(["--config", str(selected)])
        create.assert_called_once_with("selected", output=Path("runs"))

    def test_agent_defaults_and_child_configuration_isolation(self):
        config.save_config(
            {
                "server": {"platform": "other-platform", "partition": "other-queue"},
                "agent": {
                    "platform": "qpu",
                    "model": "openai:gpt-4.1",
                    "ssh": "access-node",
                    "partition": "quantum",
                    "timeout": 7200,
                    "slurm_arg": ["--account=configured"],
                    "workdir": "/shared/project",
                    "server_python": "/shared/venv/bin/python",
                    "platforms": "/shared/platforms",
                },
            }
        )
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            agent.main(
                ["--slurm-arg=--account=explicit", "--timeout", "600", "Inspect qubit 0"]
            )
        args = run.call_args.args[0]
        self.assertEqual(args.platform, "qpu")
        self.assertEqual(args.model, "openai:gpt-4.1")
        self.assertEqual(args.timeout, 600)
        self.assertEqual(args.slurm_arg, ["--account=explicit"])
        self.assertEqual(args.objective, "Inspect qubit 0")
        self.assertEqual(args.output, "runs")
        transport = agent.transport_for(args)
        self.assertEqual(transport.command, "ssh")
        self.assertIn("--no-config", transport.args[-1])
        self.assertNotIn("other-queue", transport.args[-1])

    def test_agent_no_section_inheritance_or_credentials_in_config(self):
        config.save_config({"server": {"platform": "qpu"}})
        self.assert_cli_error(
            lambda: agent.main(["--model", "openai:gpt-4.1", "Inspect"]),
            "--platform is required",
        )
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            agent.main(
                ["--no-config", "--platform", "explicit", "--model", "test", "Inspect"]
            )
        args = run.call_args.args[0]
        self.assertEqual(args.timeout, 3600)
        self.assertEqual(args.slurm_arg, [])
        self.assertIsNone(args.workdir)
        self.assertEqual(agent.transport_for(args).command, sys.executable)

    def test_effective_partition_required_and_timeout_finite(self):
        config.save_config(
            {
                "agent": {
                    "platform": "qpu", "model": "test",
                    "slurm_arg": ["--account=quantum"],
                }
            }
        )
        self.assert_cli_error(
            lambda: agent.main(["Inspect"]), "--slurm-arg requires --partition"
        )
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock):
            agent.main(["--partition", "qpu", "Inspect"])
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                self.assert_cli_error(
                    lambda: agent.main(["--partition", "qpu", "--timeout", value, "Inspect"]),
                    "positive finite number",
                )
