"""Configuration discovery, persistence and CLI precedence without hardware."""

import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from click.testing import CliRunner

from qibocal_agent import agent, config, environment
from qibocal_agent.cli import main


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            "QIBOCAL_AGENTS_CONFIG_DIR": str(self.directory),
            "XDG_CACHE_HOME": str(self.directory / "cache"),
            "XDG_DATA_HOME": str(self.directory / "data"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.path = self.directory / "agents.json"

    def cli(self, *args):
        """Run qibocal-agent; return its output, failing on errors."""
        result = CliRunner().invoke(main, list(args), catch_exceptions=False)
        self.assertEqual(result.exit_code, 0, result.output)
        return result.output.strip()

    def assert_cli_error(self, args, pattern, code=2):
        result = CliRunner().invoke(main, list(args))
        self.assertEqual(result.exit_code, code, result.output)
        self.assertRegex(result.output, pattern)

    def resolve(self, name=None, cli=None, **kwargs):
        return config.resolve("agent", cli or {}, name, **kwargs)

    def test_discovery_custom_xdg_and_home(self):
        self.assertEqual(config.get_config_file(), self.path)
        with patch.dict(
            os.environ,
            {"QIBOCAL_AGENTS_CONFIG_DIR": "", "XDG_CONFIG_HOME": str(self.directory / "xdg")},
        ):
            self.assertEqual(
                config.get_config_file(), self.directory / "xdg/qibocal/agents.json"
            )
            with (
                patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}),
                patch.object(Path, "home", return_value=self.directory),
            ):
                self.assertEqual(
                    config.get_config_file(),
                    self.directory / ".config/qibocal/agents.json",
                )

    def test_read_commands_do_not_create_files(self):
        self.assertEqual(self.cli("config", "path"), str(self.path))
        self.assertEqual(self.cli("config", "show"), "{}")
        self.assertEqual(self.cli("config"), f"Configuration file: {self.path}\n{{}}")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_bare_config_shows_location_and_settings(self):
        self.cli("config", "set", "default.platform", "qpu")
        self.assertEqual(
            self.cli("config"),
            f"Configuration file: {self.path}\n"
            + json.dumps({"agents": {"default": {"platform": "qpu"}}}, indent=2),
        )

    def test_legacy_file_is_reported_but_not_read(self):
        (self.directory / "agent.json").write_text('{"agent": {"platform": "old"}}')
        result = CliRunner().invoke(main, ["config", "show"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("agent.json is no longer read", result.stderr)
        self.assertIn("agents.default", result.stderr)
        self.assertEqual(result.stdout.strip(), "{}")

    def test_named_management_and_nested_report_settings(self):
        self.cli("config", "set", "local.platform", "local-qpu")
        self.cli("config", "set", "remote.platform", "remote-qpu")
        self.cli("config", "set", "remote.ssh", "node")
        self.cli("config", "set", "remote.objective", "Inspect only; stop on errors")
        self.cli("config", "set", "remote.report.port", "8123")
        self.assertEqual(self.cli("config", "get", "remote.ssh"), "node")
        self.assertEqual(self.cli("config", "get", "remote.report.port"), "8123")
        self.cli("config", "unset", "remote.report.port")
        self.assertNotIn("report", config.load_config()["agents"]["remote"])
        self.cli("config", "unset", "local.platform")
        self.assertNotIn("local", config.load_config()["agents"])
        self.assert_cli_error(["config", "get", "local.platform"], "not configured")

    def test_schema_validates_every_entry(self):
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
            {"agents": {"local": {"timeout": "10"}}},
            {"agents": {"local": {"timeout": float("nan")}}},
            {"agents": {"local": {"slurm_arg": "--account=quantum"}}},
            {"agents": {"local": {"platform": None}}},
            {"other": {}},
        ):
            with self.subTest(data=data), self.assertRaises(config.ConfigError):
                config.validate_config(data)

    def test_former_sections_and_keys_point_to_their_replacements(self):
        for section, target in (("agent", "agents.default"), ("report", "agents.default.report")):
            with self.subTest(section=section):
                with self.assertRaisesRegex(config.ConfigError, target):
                    config.validate_config({section: {}})
        with self.assertRaisesRegex(config.ConfigError, "renamed to platforms_dir"):
            config.validate_config({"agents": {"lab": {"platforms": "/p"}}})
        self.assert_cli_error(["config", "set", "agent.platform", "qpu"], "default.platform")
        self.assert_cli_error(["config", "set", "agents.lab.platform", "qpu"], "NAME.setting")

    def test_values_round_trip_with_their_json_types(self):
        siblings = {"servers.json": "report servers", "auth.json": "report auth"}
        for name, text in siblings.items():
            (self.directory / name).write_text(text)
        self.cli("config", "set", "default.platform", "qpu")
        self.cli("config", "set", "default.timeout", "7200")
        self.cli("config", "set", "default.slurm_arg", '["--account=quantum"]')
        self.assertEqual(self.cli("config", "get", "default.timeout"), "7200")
        self.assertEqual(
            json.loads(self.cli("config", "get", "default.slurm_arg")), ["--account=quantum"]
        )
        for key, value in (
            ("default.timeout", "abc"),
            ("default.timeout", "true"),
            ("default.slurm_arg", '"not an array"'),
            ("default.platform", ""),
            ("default.unknown", "value"),
            ("default.platform_repo", '["repo#subdirectory=../outside"]'),
            ("default.dependency", '["--target=/elsewhere"]'),
        ):
            with self.subTest(key=key, value=value):
                self.assert_cli_error(["config", "set", key, value], "Error")
        for name, text in siblings.items():
            self.assertEqual((self.directory / name).read_text(), text)

    def test_invalid_files_are_errors_and_left_untouched(self):
        self.path.write_text("{broken")
        with self.assertRaisesRegex(config.ConfigError, "Could not read"):
            config.load_config()
        self.assert_cli_error(["config", "set", "default.platform", "qpu"], "Could not read")
        self.assertEqual(self.path.read_text(), "{broken")
        self.assert_cli_error(["mcp"], "Could not read")

    def test_explicit_file_and_no_config(self):
        selected = self.directory / "other/settings.json"
        self.cli("--config", str(selected), "config", "set", "default.platform", "other")
        self.assertEqual(
            config.load_config(selected)["agents"]["default"]["platform"], "other"
        )
        self.assertFalse(self.path.exists())
        self.assertEqual(self.cli("--config", str(selected), "config", "path"), str(selected))
        self.assert_cli_error(["--config", str(self.path), "config", "show"], "not found")
        self.assert_cli_error(["--config", str(selected), "--no-config", "mcp"], "cannot be combined")
        self.assert_cli_error(["--no-config", "config"], "cannot be combined")

    def test_failed_atomic_write_preserves_original_and_cleans_temporary(self):
        config.save_config({"agents": {"default": {"platform": "original"}}})
        with patch("qibocal_agent.config.os.replace", side_effect=OSError("write denied")):
            with self.assertRaisesRegex(config.ConfigError, "write denied"):
                config.save_config({"agents": {"default": {"platform": "replacement"}}})
        self.assertEqual(config.load_config()["agents"]["default"]["platform"], "original")
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_default_is_used_without_a_name_and_names_are_isolated(self):
        config.save_config({"agents": {
            "default": {"platform": "sim", "ssh": "node", "dependency": ["simulator"]},
            "lab": {"platform": "chip"},
        }})
        default = self.resolve()
        self.assertEqual((default["name"], default["platform"], default["ssh"]), ("default", "sim", "node"))
        lab = self.resolve("lab")
        self.assertEqual((lab["platform"], lab["ssh"], lab["dependency"]), ("chip", None, []))
        with self.assertRaisesRegex(config.ConfigError, "Unknown agent: missing"):
            self.resolve("missing")
        # Built-ins still work without any stored configuration.
        adhoc = self.resolve(cli={"platform": "adhoc"}, enabled=False)
        self.assertEqual((adhoc["name"], adhoc["platform"]), ("default", "adhoc"))
        with self.assertRaisesRegex(config.ConfigError, "Unknown agent"):
            self.resolve("lab", enabled=False)

    def test_each_configuration_owns_its_folders_and_environment(self):
        config.save_config({"agents": {
            "lab": {"platform": "chip", "ssh": "node", "workdir": "./project"},
            "shared": {"platform": "chip", "server_python": "/existing/python"},
        }})
        lab = self.resolve("lab")
        self.assertEqual((lab["output"], lab["venv"]), ("lab", "lab"))
        self.assertEqual(lab["directories"], {"output": "lab", "workdir": "./project"})
        tokens = shlex.split(agent.transport_for(lab).args[-1])
        request = json.loads(next(token for token in tokens if token.startswith("{")))
        self.assertEqual(request["options"]["directories"], lab["directories"])
        self.assertEqual(request["options"]["venv"], "lab")
        self.assertNotIn(str(Path.home()), json.dumps(request["options"]))
        # An explicit interpreter replaces the implied environment.
        self.assertIsNone(self.resolve("shared")["venv"])
        # Command-line locations keep their current-directory meaning.
        explicit = self.resolve("lab", {"output": "cli-runs"})
        self.assertEqual(explicit["directories"], {"workdir": "./project"})

    def test_command_line_overrides_and_conflicts(self):
        config.save_config({"agents": {"default": {
            "platform": "qpu", "slurm_arg": ["--account=stored"], "partition": "q",
            "server_python": "/existing/python",
        }}})
        values = self.resolve(cli={"slurm_arg": ["--account=cli"], "timeout": 600})
        self.assertEqual((values["slurm_arg"], values["timeout"]), (["--account=cli"], 600))
        with self.assertRaisesRegex(config.ConfigError, "cannot be combined"):
            self.resolve(cli={"venv": "lab"})
        with self.assertRaisesRegex(config.ConfigError, "--slurm-arg requires --partition"):
            self.resolve(cli={"platform": "x", "slurm_arg": ["--a"]}, enabled=False)
        with self.assertRaisesRegex(config.ConfigError, r"--platform is required \(or set default.platform\)"):
            self.resolve(enabled=False)
        with self.assertRaisesRegex(config.ConfigError, r"--model is required \(or set default.model\)"):
            self.resolve(require_model=True)
        with self.assertRaisesRegex(config.ConfigError, "positive finite"):
            self.resolve(cli={"timeout": float("inf")})

    def test_run_selects_names_objectives_and_the_model(self):
        config.save_config({"agents": {
            "default": {"platform": "sim", "model": "test"},
            "lab": {"platform": "chip", "model": "test", "objective": "Saved objective"},
        }})
        with patch("qibocal_agent.agent.run", new_callable=AsyncMock) as run:
            self.cli("run", "Calibrate qubit 0")
            values = run.call_args.args[0]
            self.assertEqual((values["name"], values["objective"]), ("default", "Calibrate qubit 0"))
            self.cli("run", "lab")
            self.assertEqual(run.call_args.args[0]["objective"], "Saved objective")
            self.cli("run", "lab", "Explicit objective", "--model", "other")
            values = run.call_args.args[0]
            self.assertEqual((values["objective"], values["model"]), ("Explicit objective", "other"))
        self.assert_cli_error(["run"], "objective is required")
        self.assert_cli_error(["--no-config", "run", "--platform", "x", "Go"], "--model is required")

    def test_mcp_serves_the_selected_configuration_without_a_model(self):
        config.save_config({"agents": {"lab": {"platform": "chip", "partition": "q"}}})
        proxy = Mock()
        with patch("qibocal_agent.remote.create_proxy", return_value=proxy) as create:
            self.cli("mcp", "lab", "--timeout", "60")
        upstream, initial, name = create.call_args.args
        self.assertEqual((initial.platform, initial.partition, initial.timeout), ("chip", "q", 60))
        self.assertEqual(name, "lab")
        self.assertTrue(upstream.enabled)
        proxy.run.assert_called_once_with(transport="stdio", show_banner=False)
        self.assert_cli_error(["--no-config", "mcp"], "--platform is required")

    def test_reports_inherit_locations_but_never_the_server_environment(self):
        config.save_config({"agents": {"lab": {
            "platform": "chip", "ssh": "node", "output": "/shared/runs", "venv": "lab-env",
            "report": {"port": 8123, "remote_port": 9000},
        }}})
        values = config.resolve("report", {}, "lab")
        self.assertEqual((values["ssh"], values["output"]), ("node", "/shared/runs"))
        self.assertEqual(values["venv"], config.REPORT_ENVIRONMENT)
        self.assertEqual((values["port"], values["remote_port"]), (8123, 9000))
        self.assertEqual(values["directories"], {"workdir": "lab"})
        with self.assertRaisesRegex(config.ConfigError, "requires --ssh"):
            config.resolve("report", {"remote_port": 9001}, enabled=False)


if __name__ == "__main__":
    unittest.main()
