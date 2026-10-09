"""Report server configuration, provisioning and SSH forwarding."""

import argparse
import contextlib
import io
import json
import os
import pty
import shlex
import socket
import subprocess
import sys
import tempfile
import termios
import textwrap
import threading
import types
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
        self.assertEqual(request["options"]["notify_ready"], 9000)
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

    def test_remote_logs_are_preserved_and_listening_signal_is_consumed(self):
        ready = threading.Event()
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs):
            report.relay_output(io.StringIO(
                "Installing report server\r\n"
                + environment.REPORT_READY_MARKER + "\r\n"
                + "channel 2: open failed: connect failed: Connection refused\r\n"
            ), ready)
        self.assertTrue(ready.is_set())
        self.assertEqual(logs.getvalue(), (
            "Installing report server\r\n"
            "channel 2: open failed: connect failed: Connection refused\r\n"
        ))

    def test_remote_http_probe_waits_for_listening_signal(self):
        process = Mock()
        process.poll.side_effect = [None, None, None, 0]
        process.wait.return_value = 0
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        ready = Mock()
        ready.wait.side_effect = [False, True]
        with (
            patch.object(report.socket, "socket"),
            patch.object(report.subprocess, "Popen", return_value=process) as spawn,
            patch.object(report, "build_opener", return_value=opener),
            patch.object(report.threading, "Event", return_value=ready),
            patch.object(report.threading, "Thread") as thread,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            report.run(report.ReportSettings(ssh="access"))
        self.assertEqual(ready.wait.call_count, 2)
        ready.set.assert_not_called()
        opener.open.assert_called_once_with(
            "http://127.0.0.1:8000/api/health", timeout=1,
        )
        self.assertEqual(spawn.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(spawn.call_args.kwargs["text"])
        thread.assert_called_once_with(
            target=report.relay_output, args=(process.stdout, ready), daemon=True,
        )
        thread.return_value.start.assert_called_once_with()
        thread.return_value.join.assert_called_once_with()
        process.stdout.close.assert_called_once_with()

    def test_remote_launch_preserves_terminal_modes_and_log_newlines(self):
        child = textwrap.dedent(f"""\
            import os
            import time
            import tty
            if os.isatty(0):
                tty.setraw(0)
            print({environment.REPORT_READY_MARKER!r}, flush=True)
            print("First log line\\r\\nSecond log line", flush=True)
            time.sleep(0.5)
        """)
        wrapper = textwrap.dedent(f"""\
            import os
            import sys
            from unittest.mock import MagicMock, patch
            from qibocal_mcp import report
            launch = report.ServerLaunch(
                command=sys.executable, args=["-c", {child!r}], env=dict(os.environ),
            )
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.status = 200
            with (
                patch.object(report, "launch_for", return_value=launch),
                patch.object(report.socket, "socket"),
                patch.object(report, "build_opener", return_value=opener),
            ):
                report.run(report.ReportSettings(ssh="access"))
        """)
        master, slave = pty.openpty()
        try:
            modes = termios.tcgetattr(slave)
            result = subprocess.run(
                [sys.executable, "-c", wrapper],
                stdin=slave, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(termios.tcgetattr(slave), modes)
            self.assertEqual(result.stdout, "http://127.0.0.1:8000\n")
            self.assertEqual(result.stderr, "First log line\nSecond log line\n")
        finally:
            os.close(slave)
            os.close(master)

    def test_remote_failure_before_listening_does_not_probe_tunnel(self):
        process = Mock()
        process.poll.return_value = 7
        process.wait.return_value = 7
        opener = Mock()
        with (
            patch.object(report.socket, "socket"),
            patch.object(report.subprocess, "Popen", return_value=process),
            patch.object(report, "build_opener", return_value=opener),
            patch.object(report.threading, "Thread"),
            self.assertRaisesRegex(environment.DeploymentError, "status 7"),
        ):
            report.run(report.ReportSettings(ssh="access"))
        opener.open.assert_not_called()

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

    def test_closed_server_connections_do_not_block_restart(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            address = listener.getsockname()
            with socket.create_connection(address) as client:
                connection, _ = listener.accept()
                with connection:
                    connection.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b"")

        process = Mock()
        process.poll.return_value = 0
        process.wait.return_value = 0
        with patch.object(report.subprocess, "Popen", return_value=process) as spawn:
            report.run(report.ReportSettings(port=address[1]))
        spawn.assert_called_once()

    def test_reuse_does_not_allow_another_active_server(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with (
                patch.object(report.subprocess, "Popen") as spawn,
                self.assertRaisesRegex(environment.DeploymentError, "Could not start"),
            ):
                report.run(report.ReportSettings(port=listener.getsockname()[1]))
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

    def test_named_agent_needs_no_location_flags(self):
        config.save_config({
            "agent": {"ssh": "unrelated", "output": "unrelated"},
            "report": {"port": 7000},
            "agents": {
                "remote": {
                    "platform": "qpu", "ssh": "node", "output": "/shared/runs",
                    "workdir": "/shared/project", "venv": "lab",
                    "report": {"port": 8123, "remote_port": 9000, "venv": "reports"},
                },
                "local": {"output": "local-runs"},
            },
        })
        with patch.object(report, "run") as run:
            report.main(["remote"])
            run.assert_called_with(report.ReportSettings(
                ssh="node", output="/shared/runs", workdir="/shared/project",
                venv="reports", port=8123, remote_port=9000,
            ))
            report.main(["local"])
            run.assert_called_with(report.ReportSettings(
                output="local-runs", workdir="local", venv="local",
                directories={"output": "local-runs", "workdir": "local"},
            ))
            report.main(["remote", "--output", "explicit", "--port", "8124"])
            self.assertEqual(run.call_args.args[0].output, "explicit")
            self.assertEqual(run.call_args.args[0].port, 8124)
            self.assertEqual(run.call_args.args[0].directories, {})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            report.main(["unknown"])

    def test_ports_are_validated(self):
        for option in ("--port", "--remote-port"):
            for value in ("0", "65536"):
                with self.subTest(option=option, value=value), self.assertRaises(SystemExit):
                    self.parse(["--no-config", option, value])

    def test_named_report_defaults_match_agent_directories(self):
        config.save_config({"agents": {"lab": {"ssh": "remote-host"}}})
        with patch.object(report, "run") as run:
            report.main(["lab"])
        settings = run.call_args.args[0]
        self.assertEqual((settings.output, settings.workdir, settings.venv), ("lab", "lab", "lab"))
        launch = report.launch_for(settings)
        tokens = shlex.split(launch.args[-1])
        self.assertEqual(tokens[:3], ["exec", "python3", "-c"])
        request = json.loads(tokens[4])
        self.assertEqual(request["options"]["directories"], {
            "output": "lab", "workdir": "lab",
        })

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
        self.assertEqual(args.directories, {})
        self.assertEqual(self.parse().directories, {"output": "report-runs"})
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
    revision = "a" * 40
    remote = revision + "\trefs/heads/main\n"
    requirement = environment.REPORT_REQUIREMENT.rsplit("@", 1)[0] + "@" + revision

    def test_latest_report_is_installed_and_launched(self):
        installed = {
            "url": "https://github.com/qiboteam/qibocal-report.git",
            "vcs_info": {"commit_id": self.revision},
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
            patch.object(environment, "command", return_value=self.remote) as command,
        ):
            python, _ = environment.provision_report({"venv": "reports"})
        self.assertEqual(python, "/env/bin/python")
        self.assertEqual(command.call_args_list[0].args[0], [
            "git", "ls-remote", "--exit-code",
            "https://github.com/qiboteam/qibocal-report.git", "refs/heads/main",
        ])
        self.assertEqual(command.call_args_list[1].args[0], [
            "install", self.requirement,
        ])
        installer.assert_any_call("/env/bin/python", "check")

    def test_current_main_is_reused_with_shared_environment_lock(self):
        installed = {
            "url": "https://github.com/qiboteam/qibocal-report.git",
            "vcs_info": {"commit_id": self.revision},
        }
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked") as locked,
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", return_value=installed),
            patch.object(environment, "installer", return_value=["check"]) as installer,
            patch.object(environment, "command", return_value=self.remote),
        ):
            environment.provision_report({"venv": "reports"})
        locked.assert_called_once_with(environment.environment_lock("/env"))
        installer.assert_called_once_with("/env/bin/python", "check")

    def test_wrong_pin_is_selectively_reinstalled(self):
        previous = {
            "url": "https://github.com/qiboteam/qibocal-report.git",
            "vcs_info": {"commit_id": "9ebca656a21fb926c943191a1fc86330a70e6fe9"},
        }
        correct = {
            "url": "https://github.com/qiboteam/qibocal-report.git",
            "vcs_info": {"commit_id": self.revision},
        }
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked"),
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", side_effect=[previous, correct]),
            patch.object(environment, "installer", return_value=["uv", "pip", "install"]),
            patch.object(environment, "command", return_value=self.remote) as command,
        ):
            environment.provision_report({"venv": "reports"})
        self.assertEqual(command.call_args_list[1].args[0], [
            "uv", "pip", "install", "--reinstall", "--no-deps",
            self.requirement,
        ])

    def test_incorrect_installed_revision_fails_before_launch(self):
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked"),
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", return_value=None),
            patch.object(environment, "installer", return_value=["install"]),
            patch.object(environment, "command", return_value=self.remote),
            self.assertRaisesRegex(environment.DeploymentError, "latest main commit"),
        ):
            environment.provision_report({"venv": "reports"})

    def test_invalid_main_resolution_prevents_installation(self):
        for remote in ("", "main\trefs/heads/main\n", self.revision + "\trefs/heads/other\n"):
            with (
                self.subTest(remote=remote),
                patch.object(environment, "environment_path", return_value=Path("/env")),
                patch.object(environment, "locked"),
                patch.object(environment, "ensure_python", return_value="/env/bin/python"),
                patch.object(environment, "installed_distribution_source", return_value=None),
                patch.object(environment, "installer") as installer,
                patch.object(environment, "command", return_value=remote),
                self.assertRaisesRegex(environment.DeploymentError, "Could not resolve"),
            ):
                environment.provision_report({"venv": "reports"})
            installer.assert_not_called()

    def test_installer_failure_is_not_swallowed(self):
        with (
            patch.object(environment, "environment_path", return_value=Path("/env")),
            patch.object(environment, "locked"),
            patch.object(environment, "ensure_python", return_value="/env/bin/python"),
            patch.object(environment, "installed_distribution_source", return_value=None),
            patch.object(environment, "installer", return_value=["install"]),
            patch.object(
                environment, "command",
                side_effect=[
                    self.remote, environment.DeploymentError("installation failed"),
                ],
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

    def test_remote_bootstrap_signals_only_after_port_is_listening(self):
        with (
            patch.object(
                environment, "provision_report",
                return_value=("/env/bin/python", {}),
            ),
            patch.object(environment.os, "execvpe") as execute,
            tempfile.TemporaryDirectory() as directory,
        ):
            environment.launch_report(
                {"venv": "reports", "notify_ready": 9000},
                [directory, "--host", "127.0.0.1", "--port", "9000"],
            )
        expression = execute.call_args.args[1][2]
        cli = types.ModuleType("qibocal_report.cli")
        cli.main = Mock()
        output = io.StringIO()
        with (
            patch.dict(sys.modules, {
                "qibocal_report": types.ModuleType("qibocal_report"),
                "qibocal_report.cli": cli,
            }),
            patch.object(threading, "Thread") as thread,
            patch.object(report.socket, "create_connection", side_effect=[
                ConnectionRefusedError(), TimeoutError(), MagicMock(),
            ]) as connect,
            patch.object(report.time, "sleep") as sleep,
            contextlib.redirect_stdout(output),
        ):
            exec(expression, {})
            self.assertEqual(output.getvalue(), "")
            cli.main.assert_called_once_with()
            self.assertTrue(thread.call_args.kwargs["daemon"])
            thread.call_args.kwargs["target"]()
        self.assertEqual(connect.call_count, 3)
        connect.assert_called_with(("127.0.0.1", 9000), timeout=1)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(output.getvalue(), "\n" + environment.REPORT_READY_MARKER + "\n")


if __name__ == "__main__":
    unittest.main()
