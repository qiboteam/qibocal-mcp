"""Report server configuration, provisioning and SSH forwarding."""

import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.error import URLError

from qibocal_mcp import config, environment, report


class ReportLaunchTests(unittest.TestCase):
    def test_local_launch_uses_managed_environment_and_output(self):
        settings = report.ReportSettings(
            output="calibration runs", workdir="/shared/project", venv="reports",
            port=8123, remote_port=8123,
        )
        launch = report.launch_for(settings)
        self.assertEqual(launch.command, sys.executable)
        self.assertEqual(launch.cwd, "/shared/project")
        request = json.loads(launch.args[2])
        self.assertEqual(request, {
            "application": "report", "options": {"venv": "reports"}
        })
        self.assertEqual(
            launch.args[-5:],
            ["calibration runs", "--host", "127.0.0.1", "--port", "8123"],
        )

    def test_ssh_launch_forwards_registration_port(self):
        settings = report.ReportSettings(
            output="/shared/runs", ssh="access-node",
            workdir="/shared/calibration project", venv="/shared/report-env",
            port=8123, remote_port=9000,
        )
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": "/tmp/agent"}):
            launch = report.launch_for(settings)
        self.assertEqual(launch.command, "ssh")
        self.assertEqual(launch.args[0], "-tt")
        self.assertIn("ExitOnForwardFailure=yes", launch.args)
        self.assertIn("127.0.0.1:8123:127.0.0.1:9000", launch.args)
        self.assertEqual(launch.env, {"SSH_AUTH_SOCK": "/tmp/agent"})
        tokens = shlex.split(launch.args[-1])
        self.assertEqual(tokens[:4], ["cd", "/shared/calibration project", "&&", "exec"])
        self.assertEqual(tokens[4:6], ["python3", "-c"])
        self.assertIn("def launch_report", tokens[6])
        request = json.loads(tokens[7])
        self.assertEqual(request["options"]["venv"], "/shared/report-env")
        self.assertEqual(tokens[-5:], [
            "/shared/runs", "--host", "127.0.0.1", "--port", "9000",
        ])

    def test_run_prints_frontend_url_and_waits_for_server(self):
        launch = report.launch_for(report.ReportSettings(port=8123, remote_port=8123))
        output = io.StringIO()
        process = Mock()
        process.poll.side_effect = [None, None, 0]
        process.wait.return_value = 0
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        with (
            patch.object(report, "launch_for", return_value=launch),
            patch.object(report.socket, "socket"),
            patch.object(report, "build_opener", return_value=opener),
            patch.object(report.subprocess, "Popen", return_value=process) as run,
            contextlib.redirect_stdout(output),
        ):
            report.run(report.ReportSettings(port=8123, remote_port=8123))
        self.assertEqual(output.getvalue(), "http://127.0.0.1:8123\n")
        run.assert_called_once_with(
            [launch.command, *launch.args], env=launch.env, cwd=launch.cwd,
            stdout=sys.stderr, start_new_session=True,
        )
        opener.open.assert_called_once_with(
            "http://127.0.0.1:8123/api/health", timeout=1
        )

    def test_server_failure_is_reported(self):
        output = io.StringIO()
        process = Mock()
        process.poll.return_value = 7
        process.wait.return_value = 7
        with (
            patch.object(report.socket, "socket"),
            patch.object(report.subprocess, "Popen", return_value=process),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(environment.DeploymentError, "status 7"),
        ):
            report.run(report.ReportSettings())
        self.assertEqual(output.getvalue(), "")

    def test_interrupt_stops_the_owned_process_group(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        opener = Mock()
        opener.open.side_effect = KeyboardInterrupt
        with (
            patch.object(report.socket, "socket"),
            patch.object(report.subprocess, "Popen", return_value=process),
            patch.object(report, "build_opener", return_value=opener),
            patch.object(report.os, "killpg") as kill,
        ):
            report.run(report.ReportSettings())
        kill.assert_called_once_with(1234, report.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=10)

    def test_url_waits_for_http_readiness(self):
        process = Mock()
        process.poll.side_effect = [None, None, None, 0]
        process.wait.return_value = 0
        response = Mock()
        response.__enter__ = Mock(return_value=Mock(status=200))
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.side_effect = [URLError("not ready"), response]
        output = io.StringIO()
        with (
            patch.object(report.socket, "socket"),
            patch.object(report.subprocess, "Popen", return_value=process),
            patch.object(report, "build_opener", return_value=opener),
            patch.object(report.time, "sleep"),
            contextlib.redirect_stdout(output),
        ):
            report.run(report.ReportSettings())
        self.assertEqual(output.getvalue(), "http://127.0.0.1:8000\n")
        self.assertEqual(opener.open.call_count, 2)

    def test_occupied_port_does_not_start_a_process(self):
        with (
            patch.object(report.socket, "socket") as socket,
            patch.object(report.subprocess, "Popen") as spawn,
            self.assertRaisesRegex(environment.DeploymentError, "Could not start"),
        ):
            socket.return_value.__enter__.return_value.bind.side_effect = OSError("in use")
            report.run(report.ReportSettings())
        spawn.assert_not_called()


class ReportConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        settings = patch.dict(
            os.environ, {"QIBOCAL_MCP_CONFIG_DIR": str(self.directory)}
        )
        settings.start()
        self.addCleanup(settings.stop)

    def parse(self, arguments=()):
        parser = argparse.ArgumentParser(argument_default=argparse.SUPPRESS)
        for name in ("output", "ssh", "workdir", "venv"):
            parser.add_argument("--" + name)
        parser.add_argument("--port", type=int)
        parser.add_argument("--remote-port", type=int)
        return config.parse_settings(parser, "report", list(arguments))

    def test_agent_location_is_inherited_and_report_values_override_it(self):
        config.save_config({
            "agent": {
                "platform": "qpu", "model": "test", "output": "/shared/runs",
                "ssh": "access", "workdir": "/shared/project", "venv": "agent-env",
            },
            "report": {"port": 8123, "remote_port": 9000},
        })
        args = self.parse()
        self.assertEqual(args.output, "/shared/runs")
        self.assertEqual(args.ssh, "access")
        self.assertEqual(args.workdir, "/shared/project")
        self.assertEqual(args.venv, "agent-env")
        self.assertEqual((args.port, args.remote_port), (8123, 9000))
        args = self.parse(["--output", "other", "--port", "7000"])
        self.assertEqual(args.output, "other")
        self.assertEqual((args.port, args.remote_port), (7000, 9000))

    def test_defaults_are_managed_and_use_one_port(self):
        args = self.parse(["--no-config"])
        self.assertEqual(args.output, "runs")
        self.assertEqual(args.venv, "qibocal-report")
        self.assertEqual((args.port, args.remote_port), (8000, 8000))

    def test_ports_are_validated(self):
        for option in ("--port", "--remote-port"):
            for value in ("0", "65536"):
                with self.subTest(option=option, value=value), self.assertRaises(SystemExit):
                    self.parse(["--no-config", option, value])

    def test_distinct_remote_port_requires_ssh(self):
        with self.assertRaises(SystemExit):
            self.parse(["--no-config", "--remote-port", "9000"])

    def test_report_section_and_cli_override_inherited_values(self):
        config.save_config({
            "agent": {"output": "agent-runs", "venv": "agent-env"},
            "report": {"output": "report-runs", "venv": "report-env"},
        })
        args = self.parse(["--output", "cli-runs"])
        self.assertEqual(args.output, "cli-runs")
        self.assertEqual(args.venv, "report-env")
        self.assertEqual(self.parse(["--no-config"]).output, "runs")

    def test_explicit_config_and_missing_file(self):
        path = self.directory / "project.json"
        config.save_config({"agent": {"output": "project-runs"}}, path)
        self.assertEqual(self.parse(["--config", str(path)]).output, "project-runs")
        with self.assertRaises(SystemExit):
            self.parse(["--config", str(self.directory / "missing.json")])

    def test_empty_venv_is_not_masked_by_default(self):
        with self.assertRaises(SystemExit):
            self.parse(["--no-config", "--venv", ""])

    def test_invalid_report_config_is_rejected(self):
        for data in (
            {"report": {"port": True}},
            {"report": {"port": "8000"}},
            {"report": {"platform": "qpu"}},
        ):
            with self.subTest(data=data), self.assertRaises(config.ConfigError):
                config.validate_config(data)


class ReportProvisionTests(unittest.TestCase):
    def test_pinned_report_is_installed_and_launched(self):
        revision = environment.REPORT_REQUIREMENT.rsplit("@", 1)[1]
        installed = {
            "url": "https://github.com/qiboteam/qibocal-report.git",
            "vcs_info": {"commit_id": revision},
        }
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked"),
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(
                environment, "installed_distribution_source",
                side_effect=[None, installed],
            ),
            patch.object(environment, "installer", return_value=["install"]) as installer,
            patch.object(environment, "command") as command,
        ):
            python, _ = environment.provision_report({"venv": "reports"})
        self.assertEqual(python, "/env/bin/python")
        self.assertEqual(command.call_args_list[0].args[0], [
            "install", environment.REPORT_REQUIREMENT,
        ])
        installer.assert_any_call("/env/bin/python", "check")

    def test_correct_pin_is_reused_with_shared_environment_lock(self):
        installed = {
            "url": "https://github.com/qiboteam/qibocal-report.git",
            "vcs_info": {"commit_id": environment.REPORT_REQUIREMENT.rsplit("@", 1)[1]},
        }
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked") as locked,
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", return_value=installed),
            patch.object(environment, "installer", return_value=["check"]) as installer,
            patch.object(environment, "command"),
        ):
            environment.provision_report({"venv": "reports"})
        locked.assert_called_once_with(environment.environment_lock("/env"))
        installer.assert_called_once_with("/env/bin/python", "check")

    def test_wrong_pin_is_selectively_reinstalled(self):
        correct = {
            "url": "https://github.com/qiboteam/qibocal-report.git",
            "vcs_info": {"commit_id": environment.REPORT_REQUIREMENT.rsplit("@", 1)[1]},
        }
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked"),
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", side_effect=[{}, correct]),
            patch.object(environment, "installer", return_value=["uv", "pip", "install"]),
            patch.object(environment, "command") as command,
        ):
            environment.provision_report({"venv": "reports"})
        self.assertEqual(command.call_args_list[0].args[0], [
            "uv", "pip", "install", "--reinstall", "--no-deps",
            environment.REPORT_REQUIREMENT,
        ])

    def test_unresolved_pin_fails_before_launch(self):
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked"),
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", return_value=None),
            patch.object(environment, "installer", return_value=["install"]),
            patch.object(environment, "command"),
            self.assertRaisesRegex(environment.DeploymentError, "required commit"),
        ):
            environment.provision_report({"venv": "reports"})

    def test_installer_failure_is_not_swallowed(self):
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked"),
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", return_value=None),
            patch.object(environment, "installer", return_value=["install"]),
            patch.object(
                environment, "command",
                side_effect=environment.DeploymentError("installation failed"),
            ),
            self.assertRaisesRegex(environment.DeploymentError, "installation failed"),
        ):
            environment.provision_report({"venv": "reports"})

    def test_report_cli_exec_uses_click_command_shape(self):
        with (
            patch.object(
                environment, "provision_report",
                return_value=("/env/bin/python", {"HOME": "/home/user"}),
            ),
            patch.object(environment.os, "execvpe") as execute,
            tempfile.TemporaryDirectory() as directory,
        ):
            environment.launch_report(
                {"venv": "reports"},
                [directory + "/runs", "--host", "127.0.0.1", "--port", "8000"],
            )
            self.assertTrue((Path(directory) / "runs").is_dir())
        arguments = execute.call_args.args[1]
        self.assertEqual(arguments[3:5], ["report", "server"])
        self.assertEqual(arguments[-5:], [
            directory + "/runs", "--host", "127.0.0.1", "--port", "8000",
        ])


if __name__ == "__main__":
    unittest.main()
