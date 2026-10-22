"""Provisioning without a remote host, hardware or third-party installations."""

import base64
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from qibocal_agent import environment


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        settings = patch.dict(os.environ, {
            "XDG_CACHE_HOME": str(self.directory),
            "XDG_DATA_HOME": str(self.directory / "data"),
        })
        settings.start()
        self.addCleanup(settings.stop)

    def options(self, **changes):
        values = {
            "venv": None, "python": sys.executable, "platform": "qpu",
            "platforms_dir": None, "platform_repo": [], "dependency": [],
        }
        return values | changes

    def test_environment_names_and_explicit_paths(self):
        self.assertEqual(
            environment.environment_path("lab"),
            self.directory / "qibocal/agents/envs/lab",
        )
        for value in ("./lab", "relative/lab", "../lab", "."):
            self.assertEqual(
                environment.environment_path(value),
                (self.directory / "qibocal/agents/envs" / value).resolve(),
            )
        self.assertEqual(environment.environment_path("~/lab"), Path.home() / "lab")
        self.assertEqual(environment.environment_path("/shared/lab"), Path("/shared/lab"))
        for value in ("", " "):
            with self.subTest(value=value), self.assertRaises(environment.DeploymentError):
                environment.environment_path(value)

    def test_directories_are_resolved_on_the_execution_host_before_chdir(self):
        options = {"directories": {
            "output": "nested/my-agent", "workdir": "my-agent",
        }}
        root = self.directory / "qibocal/agents"
        runs = self.directory / "data/qibocal/agents/runs"
        for report in (False, True):
            with self.subTest(report=report):
                arguments = ["old-output"] if report else ["--output", "old-output"]
                with patch.object(environment.os, "chdir") as chdir:
                    environment.prepare_directories(options, arguments, report=report)
                self.assertEqual(arguments[-1], str(runs / "nested/my-agent"))
                chdir.assert_called_once_with(root / "work/my-agent")
                self.assertTrue((root / "work/my-agent").is_dir())

    def test_absolute_and_home_directories_are_not_rebased(self):
        for value, expected in (
            ("/shared/runs", Path("/shared/runs")),
            ("~/runs", Path.home() / "runs"),
        ):
            with self.subTest(value=value):
                self.assertEqual(environment.directory_path(value, "runs"), expected)
        with patch.object(environment.os, "chdir") as chdir:
            arguments = ["--output", "cli-runs"]
            environment.prepare_directories({}, arguments)
        self.assertEqual(arguments, ["--output", "cli-runs"])
        chdir.assert_not_called()

    def test_launch_passes_resolved_output_to_server_and_prepares_workdir(self):
        options = self.options(directories={"output": "lab", "workdir": "lab"})
        root = self.directory / "qibocal/agents"
        with (
            patch.object(environment.os, "chdir") as chdir,
            patch.object(environment, "provision", return_value=("/server/python", {})),
            patch.object(environment.os, "execvpe") as execute,
        ):
            environment.launch(options, ["--no-config", "--output", "lab"])
        chdir.assert_called_once_with(root / "work/lab")
        execute.assert_called_once_with(
            "/server/python",
            ["/server/python", "-m", "qibocal_mcp.server",
             "--no-config", "--output", str(self.directory / "data/qibocal/agents/runs/lab")],
            {},
        )

    def test_report_launch_uses_same_resolved_output_and_workdir(self):
        root = self.directory / "qibocal/agents"
        with (
            patch.object(environment.os, "chdir") as chdir,
            patch.object(environment, "provision_report", return_value=("/report/python", {})),
            patch.object(environment.os, "execvpe") as execute,
        ):
            environment.launch_report(
                {"venv": "lab", "directories": {"output": "lab", "workdir": "lab"}},
                ["lab", "--host", "127.0.0.1", "--port", "8000"],
            )
        chdir.assert_called_once_with(root / "work/lab")
        runs = self.directory / "data/qibocal/agents/runs"
        self.assertEqual(execute.call_args.args[1][-5:], [
            str(runs / "lab"), "--host", "127.0.0.1", "--port", "8000",
        ])
        self.assertTrue((runs / "lab").is_dir())

    def test_cache_default_and_empty_xdg_use_agent_namespace(self):
        with (
            patch.dict(os.environ, {"XDG_CACHE_HOME": "", "XDG_DATA_HOME": ""}),
            patch.object(Path, "home", return_value=self.directory),
            patch.dict(os.environ, {"HOME": str(self.directory)}),
        ):
            self.assertEqual(environment.cache_home(), self.directory / ".cache/qibocal/agents")
            for folder in ("envs", "work"):
                self.assertEqual(
                    environment.directory_path("lab", folder),
                    self.directory / ".cache/qibocal/agents" / folder / "lab",
                )
            # Reports are user data, not cache.
            self.assertEqual(environment.data_home(), self.directory / ".local/share/qibocal/agents")
            self.assertEqual(
                environment.directory_path("lab", "runs"),
                self.directory / ".local/share/qibocal/agents/runs/lab",
            )

    def test_repository_fragments_and_validation(self):
        self.assertEqual(
            environment.repository_spec("git@host:group/repo.git"),
            ("git@host:group/repo.git", None, "."),
        )
        self.assertEqual(
            environment.repository_spec("https://host/repo#ref=feature%2Fqpu&subdirectory=lab+platforms"),
            ("https://host/repo", "feature/qpu", "lab platforms"),
        )
        for value in (
            "", "-repo", "repo#", "repo#branch=main", "repo#ref=",
            "repo#ref=a&ref=b", "repo#ref=-branch",
            "repo#subdirectory=/tmp", "repo#subdirectory=../outside",
        ):
            with self.subTest(value=value), self.assertRaises(environment.DeploymentError):
                environment.repository_spec(value)

    def test_payload_is_stable_complete_and_excludes_private_settings(self):
        first = environment.source_payload()
        self.assertEqual(first, environment.source_payload())
        source = environment.unpack_source(first)
        self.assertEqual(source, environment.unpack_source(first))
        manifest = tomllib.loads((source / "pyproject.toml").read_text())
        self.assertEqual(manifest["project"]["name"], "qibocal-mcp")
        self.assertIn(
            "qibocal @ git+https://github.com/qiboteam/qibocal@ae0e797a8c93a1110737f7f75586e628e4a2e543",
            manifest["project"]["dependencies"],
        )
        self.assertTrue((source / "src/qibocal_mcp/server.py").is_file())
        self.assertFalse((source / "src/qibocal_mcp/environment.py").exists())
        self.assertFalse((source / "src/qibocal_mcp/config.py").exists())
        self.assertFalse((source / "agent.json").exists())
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(first)), mode="r:gz") as archive:
            self.assertFalse(any(".venv" in name or "config.json" in name for name in archive.getnames()))

    def test_wheel_installed_server_retains_pinned_dependencies(self):
        module = self.directory / "site-packages/qibocal_mcp"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        (module / "server.py").write_text("# installed server\n")
        distribution = Mock(
            version="0.1.0",
            requires=[
                "fastmcp>=3,<4",
                "qibocal @ git+https://github.com/qiboteam/qibocal@pinned",
            ],
            metadata={"Requires-Python": ">=3.12,<3.14"},
            entry_points=[],
        )
        spec = Mock(origin=str(module / "__init__.py"))
        with (
            patch.object(environment.util, "find_spec", return_value=spec),
            patch.object(environment.metadata, "distribution", return_value=distribution) as lookup,
        ):
            source = environment.unpack_source(environment.source_payload())
        lookup.assert_called_once_with("qibocal-mcp")
        manifest = tomllib.loads((source / "pyproject.toml").read_text())
        self.assertEqual(manifest["project"]["name"], "qibocal-mcp")
        self.assertEqual(
            manifest["project"]["dependencies"], distribution.requires
        )
        self.assertTrue((source / "src/qibocal_mcp/server.py").is_file())

    def test_broken_environment_is_not_recreated(self):
        path = self.directory / "broken"
        path.mkdir()
        with patch.object(environment, "command") as run:
            with self.assertRaisesRegex(environment.DeploymentError, "repair it manually"):
                environment.ensure_python(path)
        run.assert_not_called()

    def test_missing_installer_is_actionable(self):
        with (
            patch.object(environment.shutil, "which", return_value=None),
            patch.object(environment, "command", return_value="0\n"),
            patch("importlib.util.find_spec", return_value=None),
        ):
            with self.assertRaisesRegex(environment.DeploymentError, "Install uv"):
                environment.installer("/env/bin/python")

    def test_managed_path_must_be_a_virtual_environment(self):
        path = self.directory / "not-a-venv"
        (path / "bin").mkdir(parents=True)
        (path / "bin/python").symlink_to(sys.executable)
        with self.assertRaisesRegex(environment.DeploymentError, "pyvenv.cfg"):
            environment.ensure_python(path)

    def test_uv_and_external_pip_support_unseeded_environments(self):
        with patch.object(environment.shutil, "which", return_value="/tools/uv"):
            self.assertEqual(
                environment.installer("/env/bin/python"),
                ["uv", "pip", "install", "--python", "/env/bin/python"],
            )
        with (
            patch.object(environment.shutil, "which", return_value=None),
            patch.object(environment, "command", return_value="0\n"),
            patch("importlib.util.find_spec", return_value=Mock()),
        ):
            self.assertEqual(
                environment.installer("/env/bin/python"),
                [sys.executable, "-m", "pip", "--python", "/env/bin/python", "install"],
            )

    def test_command_failure_is_reported_and_stdout_is_reserved(self):
        with patch.object(environment.subprocess, "run") as run:
            run.return_value.stdout = "revision\n"
            self.assertEqual(environment.command(["git", "version"], capture=True), "revision\n")
            self.assertEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            environment.command(["installer", "install"])
            self.assertIs(run.call_args.kwargs["stdout"], sys.stderr)
            run.side_effect = subprocess.CalledProcessError(1, ["installer"])
            with self.assertRaisesRegex(environment.DeploymentError, "Deployment command failed"):
                environment.command(["installer", "install"])

    def create_repository(self):
        repository = self.directory / "local platforms"
        repository.mkdir()
        (repository / "platforms/qpu").mkdir(parents=True)
        (repository / "platforms/qpu/requirements.txt").write_text("driver==1\n")
        (repository / "requirements.txt").write_text("root-driver==1\n")
        (repository / "platforms/requirements.txt").write_text("platform-driver==1\n")
        (repository / "pyproject.toml").write_text('[project]\nname="platforms"\nversion="0.1"\n')
        for arguments in (
            ["init", "--quiet"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture"],
        ):
            environment.command(["git", "-C", str(repository), *arguments], capture=True)
        revision = environment.command(
            ["git", "-C", str(repository), "rev-parse", "HEAD"], capture=True
        ).strip()
        return repository, revision

    def commit_workspace(self, repository, server_text):
        """Commit a workspace with both packages; return the commit."""
        server = repository / "packages/mcp"
        (server / "src/qibocal_mcp").mkdir(parents=True, exist_ok=True)
        (repository / "packages/agent").mkdir(parents=True, exist_ok=True)
        (repository / "packages/agent/pyproject.toml").write_text('[project]\nname="qibocal-agent"\n')
        (server / "pyproject.toml").write_text('[project]\nname = "qibocal-mcp"\n')
        (server / "README.md").write_text("server\n")
        (server / "src/qibocal_mcp/server.py").write_text(server_text)
        if not (repository / ".git").exists():
            environment.command(["git", "init", "--quiet", str(repository)], capture=True)
        for arguments in (
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", server_text],
        ):
            environment.command(["git", "-C", str(repository), *arguments], capture=True)
        return environment.command(
            ["git", "-C", str(repository), "rev-parse", "HEAD"], capture=True
        ).strip()

    def installed_agent(self, origin):
        """Pretend qibocal-agent alone is installed, recording where it came from."""
        distribution = Mock()
        distribution.read_text.return_value = json.dumps(origin)
        return (
            patch.object(environment.util, "find_spec", return_value=None),
            patch.object(
                environment, "__file__",
                str(self.directory / "env/lib/python3.12/site-packages/qibocal_agent/environment.py"),
            ),
            patch.object(environment.metadata, "distribution", return_value=distribution),
        )

    def test_agent_installed_from_git_ships_the_server_of_the_same_commit(self):
        repository = self.directory / "workspace"
        installed = self.commit_workspace(repository, "# installed commit\n")
        self.commit_workspace(repository, "# later commit\n")
        origin = {
            "url": str(repository), "subdirectory": "packages/agent",
            "vcs_info": {"vcs": "git", "commit_id": installed},
        }
        first, second, third = self.installed_agent(origin)
        with first, second, third, patch.object(
            environment, "command", wraps=environment.command
        ) as run:
            payload = environment.source_payload()
            clones = [call for call in run.call_args_list if "clone" in call.args[0]]
            self.assertEqual(environment.source_payload(), payload)
            # The exported source is cached: the second payload fetches nothing.
            self.assertEqual(
                [call for call in run.call_args_list if "clone" in call.args[0]], clones
            )
        self.assertEqual(len(clones), 1)
        source = environment.unpack_source(payload)
        self.assertEqual(
            (source / "src/qibocal_mcp/server.py").read_text(), "# installed commit\n"
        )
        self.assertIn('name = "qibocal-mcp"', (source / "pyproject.toml").read_text())

    def test_agent_installed_from_a_local_folder_ships_its_sibling_server(self):
        repository = self.directory / "checkout"
        self.commit_workspace(repository, "# local\n")
        origin = {"url": (repository / "packages/agent").as_uri(), "dir_info": {}}
        first, second, third = self.installed_agent(origin)
        with first, second, third:
            source = environment.unpack_source(environment.source_payload())
        self.assertEqual((source / "src/qibocal_mcp/server.py").read_text(), "# local\n")

    def test_agent_without_a_known_origin_explains_how_to_deploy(self):
        first, second, third = self.installed_agent({})
        with first, second, third:
            with self.assertRaisesRegex(environment.DeploymentError, "Git repository"):
                environment.source_payload()
        for commit, subdirectory in (("main", "packages/mcp"), ("a" * 40, "../outside")):
            with self.subTest(commit=commit), self.assertRaises(environment.DeploymentError):
                environment.fetch_server_source(str(self.directory), commit, subdirectory)

    def test_real_git_clone_pin_subdirectory_and_reuse(self):
        repository, revision = self.create_repository()
        spec = f"{repository}#ref={revision}&subdirectory=platforms"
        checkout, platforms = environment.checkout_repository(spec)
        self.assertEqual(platforms, checkout / "platforms")
        self.assertEqual(
            environment.command(["git", "-C", str(checkout), "rev-parse", "HEAD"], capture=True).strip(),
            revision,
        )
        (checkout / "reviewed-local-file").write_text("Keep this")
        with patch.object(environment, "command", wraps=environment.command) as run:
            self.assertEqual(environment.checkout_repository(spec), (checkout, platforms))
        self.assertFalse(any("clone" in call.args[0] or "fetch" in call.args[0] for call in run.call_args_list))
        self.assertEqual((checkout / "reviewed-local-file").read_text(), "Keep this")
        packages, requirements = environment.repository_dependencies([(checkout, platforms)], "qpu")
        self.assertEqual(packages, [str(checkout)])
        self.assertEqual(requirements, [
            checkout / "requirements.txt",
            platforms / "requirements.txt",
            platforms / "qpu/requirements.txt",
        ])
        (checkout / "escape").symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(environment.DeploymentError, "outside"):
            environment.checkout_repository(f"{repository}#ref={revision}&subdirectory=escape")

    def test_selected_platform_requirements_follow_lookup_priority(self):
        repositories = []
        for name in ("first", "second"):
            checkout = self.directory / name
            (checkout / "qpu").mkdir(parents=True)
            (checkout / "qpu/requirements.txt").write_text(f"{name}-driver\n")
            (checkout / "unrelated").mkdir()
            (checkout / "unrelated/requirements.txt").write_text("not-selected\n")
            repositories.append((checkout, checkout))
        _, requirements = environment.repository_dependencies(repositories, "qpu")
        self.assertEqual(requirements, [self.directory / "first/qpu/requirements.txt"])
        explicit = self.directory / "explicit"
        (explicit / "qpu").mkdir(parents=True)
        (explicit / "qpu/requirements.txt").write_text("explicit-driver\n")
        _, requirements = environment.repository_dependencies(
            repositories, "qpu", [explicit, *[root for _, root in repositories]],
        )
        self.assertEqual(requirements, [explicit / "qpu/requirements.txt"])

    def test_requirements_fingerprint_tracks_includes_constraints_and_variables(self):
        root = self.directory / "requirements.txt"
        included = self.directory / "included.txt"
        constraint = self.directory / "constraints.txt"
        root.write_text("-r included.txt\n--constraint=constraints.txt\n${DRIVER_REQUIREMENT}\n")
        included.write_text("driver==1\n-r requirements.txt\n")
        constraint.write_text("dependency==2\n")
        with patch.dict(os.environ, {"DRIVER_REQUIREMENT": "extra==1"}):
            original = environment.requirements_fingerprint(root)
        self.assertEqual(set(original), {str(root), str(included), str(constraint)})
        included.write_text("driver==2\n")
        with patch.dict(os.environ, {"DRIVER_REQUIREMENT": "extra==1"}):
            changed = environment.requirements_fingerprint(root)
        self.assertNotEqual(original[str(included)], changed[str(included)])
        with patch.dict(os.environ, {"DRIVER_REQUIREMENT": "extra==2"}):
            changed = environment.requirements_fingerprint(root)
        self.assertNotEqual(original[str(root)], changed[str(root)])

    def test_local_projects_are_rebased_per_requirements_file(self):
        first = self.directory / "first repo"
        second = self.directory / "second repo"
        first.mkdir()
        second.mkdir()
        (first / "nested.txt").write_text('./driver ; python_version < "3.14"\n')
        (first / "requirements.txt").write_text(
            '-r nested.txt\n-e .\n--editable="./driver[hardware]"\n'
            '--find-links wheels\n./driver.whl; python_version < "3.14"\n'
            'some-package @ git+https://host/repo#subdirectory=package\n'
        )
        (second / "requirements.txt").write_text("-e ./driver\n")
        prepared = environment.prepared_requirements(first / "requirements.txt")
        contents = prepared.read_text()
        self.assertIn("-e " + first.as_uri(), contents)
        self.assertIn("--editable=" + (first / "driver").as_uri() + "[hardware]", contents)
        self.assertIn("--find-links " + (first / "wheels").as_uri(), contents)
        self.assertIn(
            (first / "driver.whl").as_uri() + '; python_version < "3.14"',
            contents,
        )
        self.assertIn("git+https://host/repo#subdirectory=package", contents)
        included = Path(shlex.split(contents.splitlines()[0])[1])
        self.assertIn(
            (first / "driver").as_uri() + ' ; python_version < "3.14"',
            included.read_text(),
        )
        other = environment.prepared_requirements(second / "requirements.txt").read_text()
        self.assertIn((second / "driver").as_uri(), other)
        self.assertNotIn((first / "driver").as_uri(), other)

    def test_requirements_cycles_are_errors_before_installation(self):
        root = self.directory / "requirements.txt"
        root.write_text("-r requirements.txt\n")
        with self.assertRaisesRegex(environment.DeploymentError, "Recursive requirements"):
            environment.prepared_requirements(root)

    def test_existing_qibocal_source_wins_and_reconciles_its_dependencies(self):
        source = environment.source_payload()
        retained = (
            "qibocal @ git+https://github.com/qiboteam/qibocal@" + "a" * 40
        )
        options = self.options(venv=str(self.directory / "managed"))

        def run(arguments, **kwargs):
            return "3.13\n" if "sys.version_info" in arguments[-1] else ""

        with (
            patch.object(environment, "ensure_python", return_value="/managed/python"),
            patch.object(environment, "installed_fingerprint", return_value="installed"),
            patch.object(environment, "installed_distribution_requirement", return_value=[retained]),
            patch.object(environment, "installer", return_value=["pip-install"]),
            patch.object(environment, "command", side_effect=run) as command,
        ):
            environment.provision(options, source)
        installs = [call.args[0] for call in command.call_args_list if call.args[0][0] == "pip-install"]
        self.assertEqual(installs[0][1:3], ["--no-deps", str(environment.unpack_source(source))])
        self.assertIn(retained, installs[1])
        self.assertTrue(any(item.startswith("fastmcp") for item in installs[1]))
        self.assertTrue((self.directory / "managed/.qibocal-mcp-state").is_file())

    def test_explicit_qibocal_overrides_existing_and_fallback_requirements(self):
        source = environment.source_payload()
        options = self.options(
            venv=str(self.directory / "managed"), dependency=["Qibocal==0.2.0"],
        )

        def run(arguments, **kwargs):
            return "3.13\n" if "sys.version_info" in arguments[-1] else ""

        with (
            patch.object(environment, "ensure_python", return_value="/managed/python"),
            patch.object(environment, "installed_fingerprint", return_value="installed"),
            patch.object(environment, "installed_distribution_requirement", return_value=["qibocal==0.1.0"]),
            patch.object(environment, "installer", return_value=["install"]),
            patch.object(environment, "command", side_effect=run) as command,
        ):
            environment.provision(options, source)
        installs = [
            call.args[0] for call in command.call_args_list
            if call.args[0][0] == "install" and len(call.args[0]) > 1
        ]
        self.assertIn("Qibocal==0.2.0", installs[-1])
        self.assertNotIn("qibocal==0.1.0", installs[-1])

    def test_retained_distributions_keep_their_source_and_editable_checkouts(self):
        cases = [
            (None, None),
            ({"version": "0.2.7", "direct_url": {
                "url": "https://github.com/qiboteam/qibocal",
                "vcs_info": {"vcs": "git", "commit_id": "b" * 40},
            }}, ["qibocal @ git+https://github.com/qiboteam/qibocal@" + "b" * 40]),
            ({"version": "0.2.7", "direct_url": {
                "url": "file:///home/me/qibocal", "dir_info": {"editable": True},
            }}, ["--editable", "file:///home/me/qibocal"]),
            ({"version": "0.2.7", "direct_url": {
                "url": "file:///home/me/qibocal", "dir_info": {},
            }}, ["qibocal @ file:///home/me/qibocal"]),
            ({"version": "0.2.7", "direct_url": {}}, ["qibocal==0.2.7"]),
        ]
        for installed, expected in cases:
            with (
                self.subTest(installed=installed),
                patch.object(environment, "command", return_value=json.dumps(installed)),
            ):
                self.assertEqual(
                    environment.installed_distribution_requirement("/env/python", "qibocal"),
                    expected,
                )

    def provision_calls(self, options, installer, check_error=None, broken=False):
        """Run provision with stubbed subprocesses; return the installer calls."""
        source = environment.source_payload()

        def run(arguments, **kwargs):
            if "sys.version_info" in arguments[-1]:
                return "3.13\n"
            if arguments[-1] == environment.QIBOCAL_COMPATIBILITY and check_error:
                raise environment.DeploymentError(check_error)
            if broken and arguments == installer:
                raise environment.DeploymentError("unmet requirements")
            return ""

        with (
            patch.object(environment, "ensure_python", return_value="/managed/python"),
            patch.object(environment, "installed_fingerprint", return_value="installed"),
            patch.object(environment, "installed_distribution_requirement", return_value=None),
            patch.object(environment, "installer", return_value=installer) as lookup,
            patch.object(environment, "command", side_effect=run) as command,
        ):
            environment.provision(options, source)
        calls = [call.args[0] for call in command.call_args_list]
        # The stub answers the dependency check too: keep calls with arguments.
        return [
            call for call in calls
            if call[:len(installer)] == installer and len(call) > len(installer)
        ], lookup

    def test_explicit_dependencies_override_declared_constraints_with_uv(self):
        options = self.options(
            venv=str(self.directory / "managed"), dependency=["qibolab==0.2.16"],
        )
        uv = ["uv", "pip", "install", "--python", "/managed/python"]
        installs, _ = self.provision_calls(options, uv)
        override = installs[-1][installs[-1].index("--override") + 1]
        self.assertEqual(Path(override).read_text(), "qibolab==0.2.16\n")
        # Without an explicit Qibocal entry, the fallback pin is required too.
        self.assertTrue(any(item.startswith("qibocal @ git+") for item in installs[-1]))
        pip = ["/managed/python", "-m", "pip", "install"]
        installs, _ = self.provision_calls(
            options | {"venv": str(self.directory / "other")}, pip,
        )
        self.assertNotIn("--override", installs[-1])

    def test_unmet_requirements_only_warn_under_explicit_overrides(self):
        uv = ["uv", "pip", "install"]
        options = self.options(venv=str(self.directory / "managed"))
        with self.assertRaisesRegex(environment.DeploymentError, "unmet"):
            self.provision_calls(options, uv, broken=True)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.provision_calls(
                options | {"dependency": ["qibolab==0.2.16"]}, uv, broken=True,
            )
        self.assertIn("intentionally override", stderr.getvalue())

    def test_incompatible_qibocal_stops_before_recording_success(self):
        options = self.options(
            venv=str(self.directory / "managed"), dependency=["qibocal==0.2.7"],
        )
        with self.assertRaisesRegex(environment.DeploymentError, "qiboteam/qibocal#1722"):
            self.provision_calls(options, ["install"], check_error="Executor.update is missing")
        self.assertFalse((self.directory / "managed/.qibocal-mcp-state").exists())

    def test_nothing_to_install_needs_no_installer(self):
        with patch.object(environment, "cache_home", return_value=self.directory / "cache"):
            installs, lookup = self.provision_calls(self.options(), ["install"])
        self.assertEqual(installs, [])
        lookup.assert_not_called()

    def test_existing_environment_is_used_in_place(self):
        path = self.directory / "existing"
        (path / "bin").mkdir(parents=True)
        (path / "bin/python").symlink_to(sys.executable)
        (path / "pyvenv.cfg").write_text("home = /usr/bin\n")
        with patch.object(environment, "command") as run:
            self.assertEqual(environment.ensure_python(path), str(path / "bin/python"))
        run.assert_not_called()
        self.assertEqual(sorted(item.name for item in path.iterdir()), ["bin", "pyvenv.cfg"])

    def test_provision_merges_sources_and_reconciles_only_when_needed(self):
        repository, revision = self.create_repository()
        source = environment.source_payload()
        installs = []

        def run(arguments, **kwargs):
            if arguments[0] == "install":
                installs.append(arguments)
                return ""
            if arguments[0] == "check":
                return ""
            if arguments[:1] == ["git"]:
                return environment_command(arguments, **kwargs)
            if "sys.version_info" in arguments[-1]:
                return "3.13\n"
            return ""

        environment_command = environment.command
        options = self.options(
            venv=str(self.directory / "managed"),
            platform_repo=[f"{repository}#ref={revision}&subdirectory=platforms"],
            dependency=["extra-driver>=1"],
        )
        with (
            patch.object(environment, "ensure_python", return_value="/managed/bin/python"),
            patch.object(environment, "installer", side_effect=lambda python, operation="install": [operation]),
            patch.object(environment, "installed_fingerprint", return_value="installed"),
            patch.object(environment, "installed_distribution_requirement", return_value=[
                "qibocal @ git+https://github.com/qiboteam/qibocal@"
                "ae0e797a8c93a1110737f7f75586e628e4a2e543"
            ]),
            patch.object(environment, "command", side_effect=run),
            patch.dict(os.environ, {"QIBOLAB_PLATFORMS": "/inherited"}),
        ):
            python, env = environment.provision(options, source)
            self.assertEqual(python, "/managed/bin/python")
            self.assertTrue(env["QIBOLAB_PLATFORMS"].endswith(os.pathsep + "/inherited"))
            self.assertEqual(len(installs), 2)
            self.assertIn("--no-deps", installs[0])
            self.assertIn("extra-driver>=1", installs[1])
            self.assertIn("--editable", installs[1])
            self.assertEqual(installs[1].count("--requirement"), 3)
            environment.provision(options, source)
            self.assertEqual(len(installs), 2)
            environment.provision(options | {"dependency": ["extra-driver>=2"]}, source)
            self.assertEqual(len(installs), 4)
            with patch.object(environment, "installed_fingerprint", return_value="manually-modified"):
                environment.provision(options | {"dependency": ["extra-driver>=2"]}, source)
            self.assertEqual(len(installs), 6)
            # Explicit roots override inherited values, but retain repository roots.
            _, env = environment.provision(options | {"platforms_dir": "/explicit:/second"}, source)
            self.assertTrue(env["QIBOLAB_PLATFORMS"].startswith("/explicit:/second" + os.pathsep))
            self.assertNotIn("/inherited", env["QIBOLAB_PLATFORMS"])
            # "~" refers to the execution host's home, expanded per entry.
            with patch.dict(os.environ, {"HOME": "/remote/home"}):
                _, env = environment.provision(
                    options | {"platforms_dir": "~/platforms:/absolute"}, source
                )
            self.assertTrue(env["QIBOLAB_PLATFORMS"].startswith(
                "/remote/home/platforms:/absolute" + os.pathsep
            ))

    def test_install_failure_does_not_record_success(self):
        def run(arguments, **kwargs):
            if "sys.version_info" in arguments[-1]:
                return "3.13\n"
            raise environment.DeploymentError("dependency conflict")

        with (
            patch.object(environment, "command", side_effect=run),
            patch.object(environment, "installed_fingerprint", return_value="installed"),
            patch.object(environment, "installer", return_value=["installer"]),
        ):
            with self.assertRaisesRegex(environment.DeploymentError, "dependency conflict"):
                environment.provision(self.options(dependency=["incompatible"]))
        self.assertFalse((environment.cache_home() / "states").exists())

    def test_launch_provisions_distinct_workers_and_executes_with_effective_environment(self):
        options = self.options(worker_python="/worker/python")
        with (
            patch.object(environment, "provision", return_value=("/server/python", {"QIBOLAB_PLATFORMS": "/repos"})) as provision,
            patch.object(environment.os, "execvpe") as execute,
        ):
            environment.launch(options, ["--no-config", "--platform", "qpu"])
        self.assertEqual(provision.call_args_list[0].args[0]["python"], "/worker/python")
        self.assertEqual(provision.call_args_list[1].args[0]["python"], sys.executable)
        execute.assert_called_once_with(
            "/server/python",
            ["/server/python", "-m", "qibocal_mcp.server", "--no-config", "--platform", "qpu"],
            {"QIBOLAB_PLATFORMS": "/repos"},
        )


if __name__ == "__main__":
    unittest.main()
