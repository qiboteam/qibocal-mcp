"""Provisioning without a remote host, hardware or third-party installations."""

import base64
import io
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

from qibocal_mcp import environment


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        settings = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.directory)})
        settings.start()
        self.addCleanup(settings.stop)

    def options(self, **changes):
        values = {
            "venv": None, "python": sys.executable, "platform": "qpu",
            "platforms": None, "platform_repo": [], "dependency": [],
        }
        return values | changes

    def test_environment_names_and_explicit_paths(self):
        self.assertEqual(
            environment.environment_path("lab"),
            self.directory / "qibocal/agent/envs/lab",
        )
        for value in ("./lab", "relative/lab", "../lab", "."):
            self.assertEqual(
                environment.environment_path(value),
                (self.directory / "qibocal/agent/envs" / value).resolve(),
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
        root = self.directory / "qibocal/agent"
        for report in (False, True):
            with self.subTest(report=report):
                arguments = ["old-output"] if report else ["--output", "old-output"]
                with patch.object(environment.os, "chdir") as chdir:
                    environment.prepare_directories(options, arguments, report=report)
                self.assertEqual(arguments[-1], str(root / "runs/nested/my-agent"))
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
        root = self.directory / "qibocal/agent"
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
             "--no-config", "--output", str(root / "runs/lab")],
            {},
        )

    def test_report_launch_uses_same_resolved_output_and_workdir(self):
        root = self.directory / "qibocal/agent"
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
        self.assertEqual(execute.call_args.args[1][-5:], [
            str(root / "runs/lab"), "--host", "127.0.0.1", "--port", "8000",
        ])
        self.assertTrue((root / "runs/lab").is_dir())

    def test_cache_default_and_empty_xdg_use_agent_namespace(self):
        with (
            patch.dict(os.environ, {"XDG_CACHE_HOME": ""}),
            patch.object(Path, "home", return_value=self.directory),
            patch.dict(os.environ, {"HOME": str(self.directory)}),
        ):
            self.assertEqual(environment.cache_home(), self.directory / ".cache/qibocal/agent")
            for folder in ("envs", "work", "runs"):
                self.assertEqual(
                    environment.directory_path("lab", folder),
                    self.directory / ".cache/qibocal/agent" / folder / "lab",
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
        self.assertEqual(manifest["project"]["name"], "qibocal-agent")
        self.assertIn(
            "qibocal @ git+https://github.com/qiboteam/qibocal@ae0e797a8c93a1110737f7f75586e628e4a2e543",
            manifest["project"]["dependencies"],
        )
        self.assertTrue((source / "src/qibocal_mcp/server.py").is_file())
        self.assertFalse((source / "agent.json").exists())
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(first)), mode="r:gz") as archive:
            self.assertFalse(any(".venv" in name or "config.json" in name for name in archive.getnames()))

    def test_wheel_installed_agent_retains_pinned_dependencies(self):
        module = self.directory / "site-packages/qibocal_mcp"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        (module / "environment.py").write_text("# installed bootstrap\n")
        distribution = Mock(
            version="0.1.0",
            requires=[
                "fastmcp>=3,<4",
                "qibocal @ git+https://github.com/qiboteam/qibocal@pinned",
                'pydantic-ai-slim ; extra == "agent"',
            ],
            metadata={"Requires-Python": ">=3.12,<3.14"},
            entry_points=[],
        )
        with (
            patch.object(environment, "__file__", str(module / "environment.py")),
            patch.object(environment.metadata, "distribution", return_value=distribution) as lookup,
        ):
            source = environment.unpack_source(environment.source_payload())
        lookup.assert_called_once_with("qibocal-agent")
        manifest = tomllib.loads((source / "pyproject.toml").read_text())
        self.assertEqual(manifest["project"]["name"], "qibocal-agent")
        self.assertEqual(
            manifest["project"]["dependencies"], distribution.requires[:2]
        )
        self.assertTrue((source / "src/qibocal_mcp/environment.py").is_file())

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

    def test_same_version_wrong_git_pin_is_force_reinstalled_and_verified(self):
        source = environment.source_payload()
        expected = {
            "url": "https://github.com/qiboteam/qibocal",
            "vcs_info": {"commit_id": "ae0e797a8c93a1110737f7f75586e628e4a2e543"},
        }
        wrong = {**expected, "vcs_info": {"commit_id": "a" * 40}}
        options = self.options(venv=str(self.directory / "managed"))

        def run(arguments, **kwargs):
            return "3.13\n" if "sys.version_info" in arguments[-1] else ""

        with (
            patch.object(environment, "ensure_python", return_value="/managed/python"),
            patch.object(environment, "installed_fingerprint", return_value="installed"),
            patch.object(environment, "installed_qibocal_source", side_effect=[wrong, expected]),
            patch.object(environment, "installer", return_value=["pip-install"]),
            patch.object(environment, "command", side_effect=run) as command,
        ):
            environment.provision(options, source)
        forced = [call.args[0] for call in command.call_args_list if "--force-reinstall" in call.args[0]]
        self.assertEqual(len(forced), 1)
        self.assertIn("--no-deps", forced[0])
        self.assertTrue(forced[0][-1].endswith(expected["vcs_info"]["commit_id"]))
        self.assertTrue((self.directory / "managed/.qibocal-mcp-state").is_file())
        with (
            patch.object(environment, "ensure_python", return_value="/managed/python"),
            patch.object(environment, "installed_fingerprint", return_value="installed"),
            patch.object(environment, "installed_qibocal_source", return_value=wrong),
            patch.object(environment, "installer", return_value=["pip-install"]),
            patch.object(environment, "command", side_effect=run),
        ):
            (self.directory / "managed/.qibocal-mcp-state").unlink()
            with self.assertRaisesRegex(environment.DeploymentError, "does not match"):
                environment.provision(options, source)
        self.assertFalse((self.directory / "managed/.qibocal-mcp-state").exists())

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
            patch.object(environment, "installed_qibocal_source", return_value={
                "url": "https://github.com/qiboteam/qibocal",
                "vcs_info": {"commit_id": "ae0e797a8c93a1110737f7f75586e628e4a2e543"},
            }),
            patch.object(environment, "command", side_effect=run),
            patch.dict(os.environ, {"QIBOLAB_PLATFORMS": "/inherited"}),
        ):
            python, env = environment.provision(options, source)
            self.assertEqual(python, "/managed/bin/python")
            self.assertTrue(env["QIBOLAB_PLATFORMS"].endswith(os.pathsep + "/inherited"))
            self.assertEqual(len(installs), 1)
            self.assertIn("extra-driver>=1", installs[0])
            self.assertIn("--editable", installs[0])
            self.assertEqual(installs[0].count("--requirement"), 3)
            environment.provision(options, source)
            self.assertEqual(len(installs), 1)
            environment.provision(options | {"dependency": ["extra-driver>=2"]}, source)
            self.assertEqual(len(installs), 2)
            with patch.object(environment, "installed_fingerprint", return_value="manually-modified"):
                environment.provision(options | {"dependency": ["extra-driver>=2"]}, source)
            self.assertEqual(len(installs), 3)
            # Explicit roots override inherited values, but retain repository roots.
            _, env = environment.provision(options | {"platforms": "/explicit:/second"}, source)
            self.assertTrue(env["QIBOLAB_PLATFORMS"].startswith("/explicit:/second" + os.pathsep))
            self.assertNotIn("/inherited", env["QIBOLAB_PLATFORMS"])

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
