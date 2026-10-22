"""Calibration session switching with synthetic callbacks and local Git state."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from qibocal.auto.operation import Protocol
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration
from qibolab import create_platform
from test_server import Inputs, acquire, fit, update

from qibocal_mcp.runtime import create_server
from qibocal_mcp.server import create_server as create_frontend
from qibocal_mcp.sessions import PlatformState, pointer_tokens, pointer_value
from qibocal_mcp.slurm import create_slurm_server
from qibocal_mcp.worker import run


def update_second_transition(results, platform, target):
    platform.calibration.single_qubits[target].qubit.frequency_12 = results.frequency[target]


def repository_git(repository, *arguments):
    return subprocess.run(
        [
            "git", "--no-pager", "-c", "commit.gpgSign=false",
            "-c", "tag.gpgSign=false", "-C", str(repository), *arguments,
        ],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "runs"
        self.platforms = []

        def create(_):
            platform = CalibrationPlatform(
                **vars(create_platform("dummy")),
                calibration=Calibration(single_qubits={0: QubitCalibration()}),
            )
            self.platforms.append(platform)
            return platform

        self.create = create
        self.enterContext(patch("qibocal_mcp.runtime.create_calibration_platform", side_effect=create))
        self.enterContext(patch("qibocal_mcp.runtime.PROTOCOLS", {
            "synthetic": Protocol(acquire, fit, update=update),
            "second": Protocol(acquire, fit, update=update_second_transition),
            "no_fit": Protocol(acquire),
            "no_update": Protocol(acquire, fit),
        }))

    async def context(self, client):
        return json.loads((await client.call_tool("get_calibration_context", {})).content[0].text)

    async def execute(self, client, value, protocol="synthetic"):
        return (await client.call_tool("execute_protocol", {
            "protocol": protocol, "parameters": {"value": value}, "targets": [0],
        })).structured_content

    async def switch(self, client, **arguments):
        return (await client.call_tool("start_session", arguments)).structured_content

    async def state(self, client, path=""):
        return (await client.call_tool("get_platform_state", {"path": path})).structured_content

    def remote_repository(self):
        remote = Path(self.directory.name) / "remote"
        platform_folder = remote / "platforms" / "synthetic"
        platform_folder.mkdir(parents=True)
        template = self.create("synthetic")
        template.dump(platform_folder)
        repository_git(remote, "init", "--quiet", "--initial-branch=main")
        repository_git(remote, "add", ".")
        repository_git(
            remote, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid",
            "commit", "--quiet", "-m", "baseline",
        )
        checkout = Path(self.directory.name) / "checkout"
        repository_git(remote, "clone", "--quiet", str(remote), str(checkout))
        baseline = repository_git(checkout, "rev-parse", "HEAD")
        update(fit(acquire(Inputs(value=5e9), [0])), template, 0)
        template.dump(platform_folder)
        repository_git(remote, "add", ".")
        repository_git(
            remote, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid",
            "commit", "--quiet", "-m", "published update",
        )
        repository_git(remote, "branch", "published")
        repository_git(remote, "tag", "published-tag")
        return remote, checkout / "platforms" / "synthetic", baseline, template

    async def test_inspection_and_explicit_edits_through_frontend(self):
        changes = {
            "/parameters/configs/0~1drive/frequency": 5e9,
            "/calibration/single_qubits/0/qubit/frequency_01": 5e9,
        }
        backend = create_server("synthetic", output=self.output)
        with patch("qibocal_mcp.server.StdioTransport", return_value=backend):
            async with Client(create_frontend("synthetic")) as client:
                original = await self.state(client)
                folder = Path(original["report_folder"])
                self.assertFalse(folder.exists())
                self.assertEqual(
                    original["value"]["parameters"]["configs"]["0/drive"]["frequency"], 4e9,
                )
                self.assertIsNone(
                    (await self.state(client, "/calibration/single_qubits/0/qubit/frequency_01"))["value"]
                )
                applied = (await client.call_tool("patch_platform", {"changes": changes})).structured_content
                self.assertEqual(applied["session_id"], original["session_id"])
                saved = PlatformState.load(Path(applied["snapshot"]))
                self.assertEqual(saved.document()["parameters"]["configs"]["0/drive"]["frequency"], 5e9)
                self.assertEqual(
                    json.loads((Path(applied["snapshot"]) / "edit.json").read_text())["changes"], changes,
                )
                baseline = PlatformState.load(folder / "platform")
                self.assertEqual(baseline.document()["parameters"]["configs"]["0/drive"]["frequency"], 4e9)
                execution = await self.execute(client, 6e9)
                self.assertEqual(
                    PlatformState.load(Path(execution["folder"]) / "execution_platform"), saved,
                )
                await self.switch(client)
                self.assertEqual((await self.state(client))["value"], saved.document())
                await self.switch(client, source="initial")
                self.assertEqual((await self.state(client))["value"], saved.document())
                self.assertEqual(PlatformState.load(Path(applied["snapshot"])), saved)

    async def test_edit_validation_is_atomic_and_initial_excludes_edits(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            original = await self.state(client)
            invalid = [
                {},
                {"parameters/settings/nshots": 5},
                {"/parameters/settings/nshots~2": 5},
                {"/parameters/settings/missing": 5},
                {"/calibration/single_qubits/999/qubit/frequency_01": 5e9},
                {"/parameters/settings": {"nshots": 5}},
                {"/parameters/settings/nshots": []},
                {"/parameters/settings/nshots": "invalid"},
                {
                    "/parameters/settings/nshots": 5,
                    "/calibration/single_qubits/0/qubit/frequency_01": "invalid",
                },
                {"/parameters/settings/nshots": float("inf")},
            ]
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaises(ToolError):
                    await client.call_tool("patch_platform", {"changes": changes})
                self.assertEqual(await self.state(client), original)
                self.assertFalse(Path(original["report_folder"]).exists())
            for path in ("/missing", "/parameters/configs/0/drive", "/parameters/settings/nshots/0"):
                with self.subTest(path=path), self.assertRaises(ToolError):
                    await self.state(client, path)
            await client.call_tool("patch_platform", {"changes": {"/parameters/settings/nshots": 5}})
            await self.switch(client, source="initial")
            self.assertEqual((await self.state(client))["value"], original["value"])

    async def test_failed_edit_snapshot_does_not_replace_accepted_state(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            first = (await client.call_tool("patch_platform", {
                "changes": {"/parameters/settings/nshots": 10},
            })).structured_content
            saved = PlatformState.load(Path(first["snapshot"]))
            accepted = await self.state(client)
            with patch.object(CalibrationPlatform, "dump", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(ToolError, "disk full"):
                    await client.call_tool("patch_platform", {
                        "changes": {"/parameters/settings/nshots": 20},
                    })
            self.assertEqual(await self.state(client), accepted)
            await client.call_tool("patch_platform", {
                "changes": {"/parameters/settings/nshots": 30},
            })
            self.assertEqual(PlatformState.load(Path(first["snapshot"])), saved)
            await self.switch(client)
            self.assertEqual((await self.state(client))["value"]["parameters"]["settings"]["nshots"], 30)

    async def test_trials_use_isolated_models_and_record_actual_state(self):
        observed = []

        def trial_acquire(pars: Inputs, platform, targets):
            observed.append(PlatformState.capture(platform))
            platform.calibration.single_qubits[0].qubit.frequency_12 = 8e9
            if pars.value < 0:
                raise RuntimeError("trial failed")
            return acquire(pars, targets)

        protocol = Protocol(trial_acquire, fit, update=update)
        overrides = {
            "/parameters/configs/0~1drive/frequency": 5e9,
            "/calibration/single_qubits/0/qubit/frequency_01": 5e9,
        }
        with patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": protocol}):
            backend = create_server("synthetic", output=self.output)
            with patch("qibocal_mcp.server.StdioTransport", return_value=backend):
                async with Client(create_frontend("synthetic")) as client:
                    original = await self.state(client)
                    arguments = {
                        "protocol": "synthetic", "parameters": {"value": 6e9},
                        "targets": [0], "platform_overrides": overrides,
                    }
                    execution = (await client.call_tool("execute_protocol", arguments)).structured_content
                    self.assertEqual(execution["platform_overrides"], overrides)
                    self.assertEqual(observed[-1].document()["parameters"]["configs"]["0/drive"]["frequency"], 5e9)
                    self.assertEqual(await self.state(client), original)
                    folder = Path(execution["report_folder"])
                    self.assertEqual(PlatformState.load(folder / "platform").document(), original["value"])
                    self.assertEqual(PlatformState.load(folder / "new_platform").document(), original["value"])
                    checkpoint = PlatformState.load(Path(execution["folder"]) / "execution_platform")
                    self.assertEqual(checkpoint.document()["parameters"]["configs"]["0/drive"]["frequency"], 5e9)
                    arguments["parameters"] = {"value": -1}
                    with self.assertRaisesRegex(ToolError, "trial failed"):
                        await client.call_tool("execute_protocol", arguments)
                    self.assertEqual(await self.state(client), original)
                    arguments["platform_overrides"] = {"/parameters/settings/missing": 1}
                    with self.assertRaisesRegex(ToolError, "Unknown platform path"):
                        await client.call_tool("execute_protocol", arguments)
                    self.assertEqual(len(observed), 2)
                    await self.switch(client, source="run", run_id=execution["run_id"])
                    selected = (await self.state(client))["value"]
                    self.assertEqual(selected["parameters"]["configs"]["0/drive"]["frequency"], 6e9)
                    self.assertEqual(selected["calibration"]["single_qubits"]["0"]["qubit"]["frequency_12"], 8e9)

    def test_json_pointer_array_indices_escaping_and_overlaps(self):
        document = {"a/b": {"~": [1, 2]}}
        self.assertEqual(pointer_value(document, pointer_tokens("/a~1b/~0/1")), 2)
        for path in ("/a~1b/~0/-1", "/a~1b/~0/01", "/a~1b/~0/2", "/a~1b/~0/-"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                pointer_value(document, pointer_tokens(path))
        state = PlatformState('{"array": [1, 2]}', "{}")
        self.assertEqual(state.patched({"/parameters/array/1": 3}).document()["parameters"]["array"], [1, 3])
        with self.assertRaisesRegex(ValueError, "overlap"):
            state.patched({"/parameters/array": None, "/parameters/array/0": 3})

    async def test_queued_edits_and_trials_preserve_accepted_handoff(self):
        from types import SimpleNamespace

        commands = []

        async def start(*command, **kwargs):
            commands.append(command)
            process = SimpleNamespace(returncode=0)

            async def communicate(request):
                try:
                    result = await run(
                        "synthetic", output=self.output,
                        snapshot=Path(command[command.index("--snapshot") + 1]) if "--snapshot" in command else None,
                        request=json.loads(request),
                        session_id=command[command.index("--session-id") + 1],
                    )
                except ToolError as error:
                    process.returncode = 1
                    return b"", str(error).encode()
                return result.model_dump_json(by_alias=True).encode(), b""

            process.communicate = communicate
            return process

        with patch("asyncio.create_subprocess_exec", side_effect=start):
            gateway = create_slurm_server(
                "synthetic", partition="qpu", output=self.output, python="python",
                workdir=self.output.parent, slurm_args=[],
            )
            async with Client(gateway) as client:
                original = await self.state(client)
                trial = (await client.call_tool("execute_protocol", {
                    "protocol": "synthetic", "parameters": {"value": 6e9}, "targets": [0],
                    "platform_overrides": {"/parameters/settings/nshots": 10},
                })).structured_content
                trial_folder = Path(trial["folder"])
                self.assertEqual(
                    PlatformState.load(trial_folder / "execution_platform").document()["parameters"]["settings"]["nshots"], 10,
                )
                self.assertEqual(await self.state(client), original)
                accepted = (await client.call_tool("patch_platform", {
                    "changes": {"/parameters/settings/nshots": 20},
                })).structured_content
                self.assertEqual((await self.state(client))["value"]["parameters"]["settings"]["nshots"], 20)
                self.assertEqual(commands[-1][commands[-1].index("--snapshot") + 1], accepted["snapshot"])
                with self.assertRaises(ToolError):
                    await client.call_tool("patch_platform", {"changes": {"/parameters/settings/nshots": "invalid"}})
                second = await self.execute(client, 7e9)
                self.assertEqual(
                    PlatformState.load(Path(second["folder"]) / "execution_platform").document()["parameters"]["settings"]["nshots"], 20,
                )
                self.assertEqual(commands[-1][commands[-1].index("--snapshot") + 1], accepted["snapshot"])
                await self.switch(client)
                self.assertEqual((await self.state(client))["value"]["parameters"]["settings"]["nshots"], 20)

    async def test_current_initial_and_handle_expiry_through_frontend(self):
        backend = create_server("synthetic", output=self.output)
        with patch("qibocal_mcp.server.StdioTransport", return_value=backend):
            async with Client(create_frontend("synthetic")) as client:
                original = await self.context(client)
                first = await self.execute(client, 5e9)
                await client.call_tool("update_platform", {"run_id": first["run_id"]})
                await self.execute(client, 6e9)
                old_folder = Path(first["report_folder"])
                old_history = (old_folder / "history.json").read_bytes()
                switched = await self.switch(client)
                self.assertNotEqual(switched["session_id"], original["session_id"])
                self.assertEqual(switched["parent_session_id"], original["session_id"])
                self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9)
                self.assertEqual(old_history, (old_folder / "history.json").read_bytes())
                folder = Path(switched["report_folder"])
                self.assertEqual(json.loads((folder / "history.json").read_text()), [])
                self.assertEqual(json.loads((folder / "session.json").read_text())["source"], "current")
                self.assertEqual((await self.context(client))["session_id"], switched["session_id"])
                with self.assertRaisesRegex(ToolError, "Unknown run_id"):
                    await client.call_tool("update_platform", {"run_id": first["run_id"]})
                new_run = await self.execute(client, 7e9)
                self.assertEqual(Path(new_run["folder"]), folder / "data" / "synthetic-0")
                await client.call_tool("update_platform", {"run_id": new_run["run_id"]})
                await self.switch(client, source="initial")
                self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9)

    async def test_initial_restores_original_baseline(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            execution = await self.execute(client, 5e9)
            await client.call_tool("update_platform", {"run_id": execution["run_id"]})
            await self.switch(client, source="initial")
            self.assertIsNone(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01)

    async def test_switch_before_execution_saves_a_baseline_without_acquiring(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            original = await self.context(client)
            self.assertFalse(Path(original["report_folder"]).exists())
            switched = await self.switch(client)
            self.assertNotEqual(switched["session_id"], original["session_id"])
            self.assertTrue((Path(switched["snapshot"]) / "parameters.json").exists())
            self.assertEqual(json.loads((Path(switched["report_folder"]) / "history.json").read_text()), [])
            self.assertIsNone(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01)

    async def test_run_applies_fit_without_mutating_original_run(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            first = await self.execute(client, 5e9)
            original_metadata = (Path(first["folder"]) / "run.json").read_bytes()
            later = await self.execute(client, 6e9, "second")
            await client.call_tool("update_platform", {"run_id": later["run_id"]})
            switched = await self.switch(client, source="run", run_id=first["run_id"])
            platform = self.platforms[-1]
            self.assertEqual(platform.calibration.single_qubits[0].qubit.frequency_01, 5e9)
            self.assertEqual(platform.config(platform.qubits[0].drive).frequency, 5e9)
            self.assertIsNone(platform.calibration.single_qubits[0].qubit.frequency_12)
            self.assertEqual(original_metadata, (Path(first["folder"]) / "run.json").read_bytes())
            self.assertFalse((Path(first["folder"]) / "platform").exists())
            baseline = Calibration.model_validate_json(
                (Path(switched["snapshot"]) / "calibration.json").read_text()
            )
            self.assertEqual(baseline.single_qubits[0].qubit.frequency_01, 5e9)

    async def test_already_updated_run_uses_execution_checkpoint_not_later_update_state(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            first = await self.execute(client, 5e9)
            later = await self.execute(client, 6e9, "second")
            await client.call_tool("update_platform", {"run_id": later["run_id"]})
            await client.call_tool("update_platform", {"run_id": first["run_id"]})
            await self.switch(client, source="run", run_id=first["run_id"])
            self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9)
            self.assertIsNone(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_12)

    async def test_invalid_sources_leave_session_and_models_unchanged(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            original = await self.context(client)
            execution = await self.execute(client, 5e9, "no_fit")
            no_update = await self.execute(client, 5e9, "no_update")
            invalid = [
                {"source": "run"},
                {"source": "initial", "run_id": execution["run_id"]},
                {"source": "current", "ref": "HEAD"},
                {"source": "current", "fetch": True},
                {"source": "initial", "remote": "origin"},
                {"source": "repository", "remote": "origin"},
                {"source": "snapshot"},
                {"source": "current", "snapshot_path": str(self.output)},
                {"source": "run", "run_id": "missing"},
                {"source": "run", "run_id": execution["run_id"]},
                {"source": "run", "run_id": no_update["run_id"]},
                {"source": "snapshot", "snapshot_path": str(self.output / "missing")},
            ]
            for arguments in invalid:
                with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                    await self.switch(client, **arguments)
                self.assertEqual((await self.context(client))["session_id"], original["session_id"])
                self.assertIsNone(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01)
            snapshot = Path(self.directory.name) / "snapshot"
            snapshot.mkdir()
            self.platforms[-1].dump(snapshot)
            (snapshot / "calibration.json").write_text("invalid JSON")
            parameters = self.platforms[-1].parameters
            with self.assertRaises(ToolError):
                await self.switch(client, source="snapshot", snapshot_path=str(snapshot))
            self.assertIs(self.platforms[-1].parameters, parameters)

    async def test_failed_fit_application_isolated_and_current_recovers_accepted_state(self):
        def broken_update(results, platform, target):
            update(results, platform, target)
            raise RuntimeError("synthetic update failed")

        protocol = Protocol(acquire, fit, update=broken_update)
        with patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": protocol}):
            async with Client(create_server("synthetic", output=self.output)) as client:
                execution = await self.execute(client, 5e9)
                original = await self.context(client)
                with self.assertRaisesRegex(ToolError, "synthetic update failed"):
                    await self.switch(client, source="run", run_id=execution["run_id"])
                self.assertIsNone(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01)
                self.assertEqual((await self.context(client))["session_id"], original["session_id"])
                with self.assertRaisesRegex(ToolError, "synthetic update failed"):
                    await client.call_tool("update_platform", {"run_id": execution["run_id"]})
                with self.assertRaisesRegex(ToolError, "failed or incomplete"):
                    await self.switch(client, source="run", run_id=execution["run_id"])
                await self.switch(client)
                self.assertIsNone(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01)

    async def test_repository_head_branch_commit_and_snapshot(self):
        repository = Path(self.directory.name) / "repo"
        platform_folder = repository / "platforms" / "synthetic"
        platform_folder.mkdir(parents=True)
        template = self.create("synthetic")
        template.dump(platform_folder)

        def git(*args):
            return subprocess.run(
                ["git", "--no-pager", "-C", str(repository), *args],
                check=True, capture_output=True, text=True,
            ).stdout.strip()

        git("init", "--quiet")
        git("add", ".")
        git("-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "commit", "--quiet", "-m", "baseline")
        baseline_commit = git("rev-parse", "HEAD")
        git("branch", "baseline")
        update(fit(acquire(Inputs(value=5e9), [0])), template, 0)
        template.dump(platform_folder)
        git("add", ".")
        git("-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "commit", "--quiet", "-m", "updated")
        updated_commit = git("rev-parse", "HEAD")
        template.calibration.single_qubits[0].qubit.frequency_01 = 9e9
        template.dump(platform_folder)
        dirty_status = git("status", "--porcelain")

        async with Client(create_server("synthetic", output=self.output)) as client:
            for ref, expected, commit in [
                (None, 5e9, updated_commit),
                ("baseline", None, baseline_commit),
                (updated_commit, 5e9, updated_commit),
            ]:
                with self.subTest(ref=ref), patch(
                    "qibocal.calibration.platform.locate_platform", return_value=platform_folder,
                ):
                    switched = await self.switch(client, source="repository", ref=ref)
                    self.assertEqual(switched["commit"], commit)
                    self.assertEqual(switched["platform_path"], "platforms/synthetic")
                    self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, expected)
                    self.assertEqual(git("status", "--porcelain"), dirty_status)
                    self.assertEqual(git("rev-parse", "HEAD"), updated_commit)
            previous = await self.context(client)
            with self.assertRaises(ToolError):
                await self.switch(client, source="repository", platform_path=str(platform_folder), ref="missing")
            with self.assertRaisesRegex(ToolError, "must not be empty"):
                await self.switch(client, source="repository", platform_path=str(platform_folder), ref="")
            self.assertEqual((await self.context(client))["session_id"], previous["session_id"])
            restored = await self.switch(
                client, source="snapshot", snapshot_path=str(platform_folder),
            )
            self.assertEqual(restored["source"], "snapshot")
            self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 9e9)

    async def test_fetch_remote_revisions_through_frontend_without_checkout(self):
        remote, platform_folder, baseline, _ = self.remote_repository()
        checkout = platform_folder.parent.parent
        published = repository_git(remote, "rev-parse", "HEAD")
        repository_git(checkout, "remote", "add", "published", str(remote))
        repository_git(checkout, "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
        (platform_folder / "calibration.json").write_text("dirty local calibration")
        dirty_status = repository_git(checkout, "status", "--porcelain")
        refs = repository_git(checkout, "show-ref")
        backend = create_server("synthetic", output=self.output)
        with (
            patch("qibocal_mcp.server.StdioTransport", return_value=backend),
            patch("qibocal.calibration.platform.locate_platform", return_value=platform_folder),
        ):
            async with Client(create_frontend("synthetic")) as client:
                offline = await self.switch(client, source="repository")
                self.assertEqual(offline["commit"], baseline)
                self.assertNotIn("fetch", offline)
                with self.assertRaises(ToolError):
                    await self.switch(client, source="repository", ref="published")
                self.assertEqual((await self.context(client))["session_id"], offline["session_id"])
                for ref, selected_remote in [
                    (None, None), ("main", None), ("published", "published"),
                    ("published-tag", None), (published, None),
                ]:
                    with self.subTest(ref=ref, remote=selected_remote):
                        switched = await self.switch(
                            client, source="repository", ref=ref, fetch=True,
                            remote=selected_remote,
                        )
                        self.assertEqual(switched["commit"], published)
                        self.assertEqual(switched["ref"], ref or "HEAD")
                        self.assertTrue(switched["fetch"])
                        self.assertEqual(switched["remote"], selected_remote or "origin")
                        self.assertEqual(
                            (await self.state(client))["value"]["calibration"]["single_qubits"]["0"]["qubit"]["frequency_01"],
                            5e9,
                        )
                        saved = json.loads((Path(switched["report_folder"]) / "session.json").read_text())
                        self.assertEqual(saved["commit"], published)
                        self.assertEqual(saved["remote"], selected_remote or "origin")
                        self.assertEqual(repository_git(checkout, "rev-parse", "HEAD"), baseline)
                        self.assertEqual(repository_git(checkout, "show-ref"), refs)
                        self.assertEqual(repository_git(checkout, "status", "--porcelain"), dirty_status)
                        self.assertEqual(
                            (platform_folder / "calibration.json").read_text(), "dirty local calibration",
                        )
                execution = await self.execute(client, 6e9)
                self.assertEqual(
                    PlatformState.load(Path(execution["folder"]) / "execution_platform")
                    .document()["calibration"]["single_qubits"]["0"]["qubit"]["frequency_01"],
                    5e9,
                )

    async def test_failed_fetch_and_invalid_remote_models_preserve_session(self):
        remote, platform_folder, _, _ = self.remote_repository()
        backend = create_server("synthetic", output=self.output)
        with patch("qibocal_mcp.server.StdioTransport", return_value=backend):
            async with Client(create_frontend("synthetic")) as client:
                original = await self.state(client)
                execution = await self.execute(client, 6e9)
                for arguments in [
                    {"remote": str(remote)},
                    {"remote": "missing"},
                    {"remote": ""},
                    {"ref": "missing"},
                    {"ref": ""},
                    {"ref": "--all"},
                    {"ref": "main:refs/heads/main"},
                    {"ref": "refs/heads/*"},
                ]:
                    with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                        await self.switch(
                            client, source="repository", platform_path=str(platform_folder),
                            fetch=True, **arguments,
                        )
                    self.assertEqual(await self.state(client), original)
                repository_git(
                    platform_folder.parent.parent, "remote", "add", "unavailable",
                    str(Path(self.directory.name) / "missing-remote"),
                )
                with self.assertRaises(ToolError):
                    await self.switch(
                        client, source="repository", platform_path=str(platform_folder),
                        fetch=True, remote="unavailable",
                    )
                (remote / "platforms" / "synthetic" / "calibration.json").write_text("invalid JSON")
                repository_git(remote, "add", ".")
                repository_git(
                    remote, "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid",
                    "commit", "--quiet", "-m", "invalid models",
                )
                with self.assertRaises(ToolError):
                    await self.switch(
                        client, source="repository", platform_path=str(platform_folder), fetch=True,
                    )
                self.assertEqual(await self.state(client), original)
                await client.call_tool("update_platform", {"run_id": execution["run_id"]})

    async def test_slurm_switches_worker_session_and_preserves_failed_switch(self):
        from types import SimpleNamespace

        commands = []

        async def start(*command, **kwargs):
            commands.append(command)
            process = SimpleNamespace(returncode=0)

            async def communicate(request):
                try:
                    result = await run(
                        "synthetic", output=self.output,
                        snapshot=Path(command[command.index("--snapshot") + 1]) if "--snapshot" in command else None,
                        request=json.loads(request),
                        session_id=command[command.index("--session-id") + 1],
                    )
                except ToolError as error:
                    process.returncode = 1
                    return b"", str(error).encode()
                return result.model_dump_json(by_alias=True).encode(), b""

            process.communicate = communicate
            return process

        with patch("asyncio.create_subprocess_exec", side_effect=start):
            gateway = create_slurm_server(
                "synthetic", output=self.output, partition="qpu",
                python="/shared/python", workdir=Path(self.directory.name), slurm_args=[],
            )
            async with Client(gateway) as client:
                first = await self.execute(client, 5e9)
                await client.call_tool("update_platform", {"run_id": first["run_id"]})
                await self.execute(client, 6e9)
                switched = await self.switch(client, source="run", run_id=first["run_id"])
                self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9)
                context = await self.context(client)
                self.assertEqual(context["session_id"], switched["session_id"])
                self.assertEqual(commands[-1][commands[-1].index("--session-id") + 1], switched["session_id"])
                self.assertEqual(commands[-1][commands[-1].index("--snapshot") + 1], switched["snapshot"])
                old_commands = len(commands)
                with self.assertRaisesRegex(ToolError, "Unknown run_id"):
                    await client.call_tool("update_platform", {"run_id": first["run_id"]})
                self.assertEqual(len(commands), old_commands)
                with self.assertRaises(ToolError):
                    await self.switch(client, source="snapshot", snapshot_path="/missing")
                self.assertEqual((await self.context(client))["session_id"], switched["session_id"])
                second = await self.execute(client, 7e9)
                self.assertEqual(Path(second["folder"]).name, "synthetic-0")
                await client.call_tool("update_platform", {"run_id": second["run_id"]})
                await self.switch(client, source="initial")
                self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9)
                self.assertEqual(json.loads((Path(first["report_folder"]) / "history.json").read_text()), ["synthetic-0", "synthetic-1"])

    async def test_slurm_fetch_restores_published_snapshot_in_later_workers(self):
        from types import SimpleNamespace

        remote, platform_folder, _, _ = self.remote_repository()
        published = repository_git(remote, "rev-parse", "HEAD")

        async def start(*command, **kwargs):
            process = SimpleNamespace(returncode=0)

            async def communicate(request):
                try:
                    result = await run(
                        "synthetic", output=self.output,
                        snapshot=Path(command[command.index("--snapshot") + 1]) if "--snapshot" in command else None,
                        request=json.loads(request),
                        session_id=command[command.index("--session-id") + 1],
                    )
                except ToolError as error:
                    process.returncode = 1
                    return b"", str(error).encode()
                return result.model_dump_json(by_alias=True).encode(), b""

            process.communicate = communicate
            return process

        with patch("asyncio.create_subprocess_exec", side_effect=start):
            gateway = create_slurm_server(
                "synthetic", output=self.output, partition="qpu", python="python",
                workdir=Path(self.directory.name), slurm_args=[],
            )
            async with Client(gateway) as client:
                switched = await self.switch(
                    client, source="repository", platform_path=str(platform_folder),
                    fetch=True, remote="origin", ref="published",
                )
                self.assertEqual(switched["commit"], published)
                self.assertEqual(switched["remote"], "origin")
                state = await self.state(client)
                self.assertEqual(state["session_id"], switched["session_id"])
                self.assertEqual(
                    state["value"]["calibration"]["single_qubits"]["0"]["qubit"]["frequency_01"], 5e9,
                )
                with self.assertRaises(ToolError):
                    await self.switch(
                        client, source="repository", platform_path=str(platform_folder),
                        fetch=True, ref="missing",
                    )
                self.assertEqual(await self.state(client), state)
                execution = await self.execute(client, 6e9)
                self.assertEqual(
                    PlatformState.load(Path(execution["folder"]) / "execution_platform"),
                    PlatformState.load(Path(switched["snapshot"])),
                )


if __name__ == "__main__":
    unittest.main()
