"""Configuration discovery, persistence and CLI precedence without hardware."""

import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from qibocal_mcp import agent, config, environment, server


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
        self.path = self.directory / "agent.json"

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
                config.get_config_file(), self.directory / "xdg/qibocal/agent.json"
            )
            with (
                patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}),
                patch.object(Path, "home", return_value=self.directory),
            ):
                self.assertEqual(
                    config.get_config_file(),
                    self.directory / ".config/qibocal/agent.json",
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

    def test_named_agent_management_and_nested_report_settings(self):
        self.command("set", "agents.local.platform", "local-qpu")
        self.command("set", "agents.remote.platform", "remote-qpu")
        self.command("set", "agents.remote.ssh", "node")
        self.command("set", "agents.remote.objective", "Inspect only; stop on errors")
        self.command("set", "agents.remote.report.port", "8123")
        self.assertEqual(self.command("get", "agents.remote.ssh"), "node")
        self.assertEqual(self.command("get", "agents.remote.report.port"), "8123")
        self.command("unset", "agents.remote.report.port")
        self.assertNotIn("report", config.load_config()["agents"]["remote"])
        self.command("unset", "agents.local.platform")
        self.assertNotIn("local", config.load_config()["agents"])
        self.assert_cli_error(
            lambda: self.command("get", "agents.local.platform"), "not configured"
        )

    def test_named_agent_launch_uses_saved_objective_and_isolates_defaults(self):
        config.save_config({
            "agent": {"platform": "unrelated", "model": "unrelated", "ssh": "other-node"},
            "agents": {
                "local": {
                    "platform": "local-qpu", "model": "test",
                    "objective": "Inspect only; stop on errors",
                },
                "remote": {
                    "platform": "remote-qpu", "model": "test",
                    "ssh": "node", "objective": "Remote inspect only",
                },
            },
        })
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            agent.main(["local"])
            args = run.call_args.args[0]
            self.assertEqual(args.platform, "local-qpu")
            self.assertIsNone(args.ssh)
            self.assertEqual(args.objective, "Inspect only; stop on errors")
            self.assertEqual((args.output, args.workdir, args.venv), ("local", "local", "local"))
            self.assertEqual(args.directories, {"output": "local", "workdir": "local"})
            agent.main(["remote", "--output", "explicit-runs", "Explicit inspect only"])
            args = run.call_args.args[0]
            self.assertEqual(args.ssh, "node")
            self.assertEqual(args.objective, "Explicit inspect only")
            self.assertEqual(args.output, "explicit-runs")
            self.assertEqual(args.directories, {"workdir": "remote"})
            self.assertIn("--no-config", agent.transport_for(args).args[-1])
        self.assert_cli_error(lambda: agent.main(["unknown"]), "Unknown agent: unknown")
        self.assert_cli_error(
            lambda: agent.main(["--no-config", "local", "Inspect"]), "Unknown agent"
        )

    def test_named_agents_require_a_bounded_objective_for_one_shot_launch(self):
        config.save_config({"agents": {"local": {"platform": "qpu", "model": "test"}}})
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            self.assert_cli_error(lambda: agent.main(["local"]), "objective is required")
        run.assert_not_called()

    def test_named_agent_schema_validates_all_entries(self):
        for data in (
            {"agents": []},
            {"agents": {"../outside": {}}},
            {"agents": {"with.dot": {}}},
            {"agents": {"local": []}},
            {"agents": {"local": {"api_key": "fixture"}}},
            {"agents": {"local": {"objective": ""}}},
            {"agents": {"local": {"report": []}}},
            {"agents": {"local": {"report": {"model": "test"}}}},
            {"agents": {"local": {"report": {"port": True}}}},
            {"agents": {"local": {"timeout": 0}}},
        ):
            with self.subTest(data=data), self.assertRaises(config.ConfigError):
                config.validate_config(data)

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
            create.assert_called_with(
                "configured", output=environment.cache_home() / "runs/configured-runs"
            )
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
        self.assertIsNone(args.model_base_url)
        self.assertEqual(agent.transport_for(args).command, sys.executable)

    def test_model_endpoint_configuration_and_cli_precedence(self):
        self.command("set", "agent.platform", "qpu")
        self.command("set", "agent.model", "openwebui:custom-model")
        self.command("set", "agent.model_base_url", "http://localhost:3000/api")
        self.assertEqual(
            self.command("get", "agent.model_base_url"), "http://localhost:3000/api"
        )
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            agent.main(["Inspect"])
            self.assertEqual(run.call_args.args[0].model_base_url, "http://localhost:3000/api")
            agent.main(["--model-base-url", "https://explicit.example/api", "Inspect"])
            self.assertEqual(run.call_args.args[0].model_base_url, "https://explicit.example/api")
        self.assert_cli_error(
            lambda: self.command("set", "agent.model_base_url", ""), "nonempty string"
        )
        self.assert_cli_error(
            lambda: self.command("set", "agent.api_key", "not-a-key"), "Unknown setting"
        )

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

    def test_deployment_settings_round_trip_and_list_replacement(self):
        self.command("set", "agent.platform", "qpu")
        self.command("set", "agent.model", "test")
        self.command("set", "agent.venv", "/shared/env")
        self.command("set", "agent.platform_repo", '["git@host:repo#subdirectory=platforms"]')
        self.command("set", "agent.dependency", '["driver>=1", "other-driver"]')
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            agent.main(["--platform-repo", "https://host/other", "--dependency", "explicit-driver", "Inspect"])
        args = run.call_args.args[0]
        self.assertEqual(args.venv, "/shared/env")
        self.assertEqual(args.platform_repo, ["https://host/other"])
        self.assertEqual(args.dependency, ["explicit-driver"])
        self.assertEqual(
            json.loads(self.command("get", "agent.dependency")),
            ["driver>=1", "other-driver"],
        )

    def test_deployment_errors_are_checked_before_startup(self):
        for key, value in (
            ("agent.platform_repo", '["repo#subdirectory=../outside"]'),
            ("server.platform_repo", '"not an array"'),
            ("agent.dependency", '["--target=/elsewhere"]'),
        ):
            with self.subTest(key=key):
                self.assert_cli_error(lambda: self.command("set", key, value), "error:")
        config.save_config({"agent": {
            "platform": "qpu", "model": "test",
            "server_python": "/existing/python",
        }})
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            self.assert_cli_error(
                lambda: agent.main(["--venv", "lab", "Inspect"]),
                "cannot be combined",
            )
        run.assert_not_called()
        with patch("qibocal_mcp.environment.launch") as launch:
            self.assert_cli_error(
                lambda: server.main(["--no-config", "--platform", "qpu", "--venv", "lab", "--worker-python", "/other/python"]),
                "cannot be combined",
            )
        launch.assert_not_called()

    def test_configured_directories_are_carried_to_the_execution_host(self):
        config.save_config({"agents": {"lab": {
            "platform": "qpu", "model": "test", "ssh": "node",
            "venv": "nested/env", "workdir": "./project", "output": "nested/runs",
        }}})
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            agent.main(["lab", "Inspect"])
        args = run.call_args.args[0]
        self.assertEqual(args.venv, "nested/env")
        self.assertEqual(args.directories, {
            "output": "nested/runs", "workdir": "./project",
        })
        transport = agent.transport_for(args)
        tokens = shlex.split(transport.args[-1])
        self.assertEqual(tokens[:3], ["exec", "python3", "-c"])
        request = json.loads(tokens[4])
        self.assertEqual(request["options"]["directories"], args.directories)
        self.assertEqual(request["options"]["venv"], "nested/env")
        self.assertNotIn(str(Path.home()), json.dumps(request["options"]))
        self.assertEqual(config.load_config()["agents"]["lab"]["output"], "nested/runs")

    def test_named_agent_explicit_interpreter_bypasses_default_environment(self):
        config.save_config({"agents": {"lab": {
            "platform": "qpu", "model": "test", "server_python": "/existing/python",
        }}})
        with patch("qibocal_mcp.agent.run", new_callable=AsyncMock) as run:
            agent.main(["lab", "Inspect"])
        args = run.call_args.args[0]
        self.assertIsNone(args.venv)
        transport = agent.transport_for(args)
        request = json.loads(transport.args[2])
        self.assertIsNone(request["options"]["venv"])
        self.assertNotIn("source", request)
        self.assertIsNone(transport.cwd)

    def test_configured_server_directories_use_xdg_cache_and_cli_overrides_do_not(self):
        config.save_config({"server": {
            "platform": "qpu", "partition": "queue",
            "output": "nested/runs", "workdir": "project",
        }})
        with (
            patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.directory / "cache")}),
            patch("qibocal_mcp.slurm.create_slurm_server") as create,
        ):
            server.main([])
            root = (self.directory / "cache/qibocal/agent").resolve()
            self.assertEqual(create.call_args.kwargs["output"], root / "runs/nested/runs")
            self.assertEqual(create.call_args.kwargs["workdir"], root / "work/project")
            self.assertTrue((root / "work/project").is_dir())
            server.main(["--output", "cli-runs", "--workdir", "cli-work"])
            self.assertEqual(create.call_args.kwargs["output"], Path("cli-runs"))
            self.assertEqual(create.call_args.kwargs["workdir"], Path("cli-work"))

    def test_standalone_managed_server_reexec_preserves_resolved_settings(self):
        with (
            patch("qibocal_mcp.environment.launch") as launch,
            patch("qibocal_mcp.server.create_server") as create,
        ):
            server.main([
                "--no-config", "--platform", "qpu", "--venv", "/shared/env",
                "--platform-repo", "git@host:repo", "--platforms", "/explicit",
                "--partition", "quantum", "--workdir", "/shared/project",
                "--dependency", "driver", "--slurm-arg=--account=lab",
            ])
        create.assert_not_called()
        options, command, payload = launch.call_args.args
        self.assertEqual(options["venv"], "/shared/env")
        self.assertEqual(options["platforms"], "/explicit")
        self.assertEqual(options["platform_repo"], ["git@host:repo"])
        self.assertEqual(options["dependency"], ["driver"])
        self.assertIsNone(options["worker_python"])
        self.assertIn("--no-config", command)
        self.assertIn("/shared/project", command)
        self.assertIn("--slurm-arg=--account=lab", command)
        self.assertNotIn("--platforms", command)
        self.assertNotIn("--worker-python", command)
        self.assertTrue(payload)
