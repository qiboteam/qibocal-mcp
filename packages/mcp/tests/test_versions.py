"""Platform versions in a local Git repository, with a bare remote."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import CallToolResult
from qibocal.auto.operation import Protocol
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration
from qibolab import create_platform
from test_server import acquire, fit, update
from test_slurm import piped

from qibocal_mcp import versions
from qibocal_mcp.runtime import create_server
from qibocal_mcp.slurm import create_slurm_server

FREQUENCY = "/calibration/single_qubits/0/qubit/frequency_01"
IDENTITY = {
    "GIT_AUTHOR_NAME": "Tests", "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "Tests", "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


def make_platform(_=None):
    return CalibrationPlatform(
        **vars(create_platform("dummy")),
        calibration=Calibration(single_qubits={0: QubitCalibration()}),
    )


class VersionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.output = root / "runs"
        self.repository = root / "repo"
        self.folder = self.repository / "platforms" / "synthetic"
        self.folder.mkdir(parents=True)
        self.enterContext(patch.dict(os.environ, IDENTITY))
        make_platform().dump(self.folder)
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "baseline")
        self.baseline = self.git("rev-parse", "HEAD")
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--quiet", "--bare", str(self.remote)], check=True)
        self.git("remote", "add", "origin", str(self.remote))
        self.enterContext(patch("qibocal_mcp.runtime.create_calibration_platform", side_effect=make_platform))
        self.enterContext(patch("qibocal_mcp.runtime.PROTOCOLS", {"synthetic": Protocol(acquire, fit, update=update)}))
        self.enterContext(patch("qibocal.calibration.platform.locate_platform", return_value=self.folder))

    def git(self, *arguments, cwd=None):
        return subprocess.run(
            ["git", "--no-pager", "-C", str(cwd or self.repository), *arguments],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    async def call(self, client, tool, **arguments):
        return (await client.call_tool(tool, arguments)).structured_content

    async def save(self, client, branch, **arguments):
        proposal = await self.call(client, "save_platform", message="Calibrate q0", branch=branch, **arguments)
        return await self.call(
            client, "save_platform", message="Calibrate q0", branch=branch,
            confirm=proposal["confirm"], **arguments,
        )

    async def test_save_extend_publish_and_check_out_a_calibration_branch(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            listing = await self.call(client, "list_versions")
            self.assertEqual(listing["checkout"], {"branch": "main", "commit": self.baseline, "clean": True})
            self.assertEqual(listing["session_base"], {"commit": self.baseline, "branch": "main"})
            self.assertEqual([version["message"] for version in listing["versions"]], ["baseline"])
            self.assertEqual((await self.call(client, "diff_platform"))["count"], 0)
            with self.assertRaisesRegex(ToolError, "Nothing to save"):
                await self.call(client, "save_platform", message="m", branch="calibration/q0")

            await self.call(client, "patch_platform", changes={FREQUENCY: 5e9})
            changes = (await self.call(client, "diff_platform"))["changes"]
            self.assertIn({"path": FREQUENCY, "old": None, "new": 5e9}, changes)
            # Proposing writes nothing; a stale token is refused.
            proposal = await self.call(
                client, "save_platform", message="Calibrate q0", branch="calibration/q0",
            )
            self.assertTrue(proposal["new_branch"])
            self.assertEqual(proposal["extends"], self.baseline)
            self.assertEqual(self.git("branch", "--list", "calibration/*"), "")
            with self.assertRaisesRegex(ToolError, "changed since"):
                await self.call(
                    client, "save_platform", message="m", branch="calibration/q0", confirm="0" * 16,
                )
            first = await self.save(client, "calibration/q0")
            self.assertEqual(self.git("rev-parse", "calibration/q0"), first["version"])
            self.assertIn("Qibocal-Session:", self.git("log", "-1", "--format=%B", "calibration/q0"))
            # The checkout is untouched.
            self.assertEqual(self.git("rev-parse", "HEAD"), self.baseline)
            self.assertEqual(self.git("status", "--porcelain"), "")

            # Later saves extend the branch linearly, and publish on request.
            await self.call(client, "patch_platform", changes={FREQUENCY: 5.1e9})
            second = await self.save(client, "calibration/q0", push=True)
            self.assertEqual(self.git("rev-parse", "calibration/q0^"), first["version"])
            self.assertEqual(second["published"], {"remote": "origin", "branch": "calibration/q0"})
            self.assertEqual(
                self.git("rev-parse", "calibration/q0", cwd=self.remote), second["version"],
            )

            # A session from another version cannot extend the branch.
            await self.call(client, "start_session", source="repository", ref="main")
            await self.call(client, "patch_platform", changes={FREQUENCY: 4e9})
            with self.assertRaisesRegex(ToolError, "would revert"):
                await self.call(client, "save_platform", message="m", branch="calibration/q0")
            # From the branch it can, and saving to the checked-out branch
            # also updates its files.
            self.assertEqual(
                (await self.call(client, "checkout_platform", ref="calibration/q0"))["branch"],
                "calibration/q0",
            )
            await self.call(client, "start_session", source="repository")
            self.assertEqual((await self.call(client, "diff_platform"))["count"], 0)
            await self.call(client, "patch_platform", changes={FREQUENCY: 5.2e9})
            third = await self.save(client, "calibration/q0")
            self.assertEqual(self.git("rev-parse", "HEAD"), third["version"])
            self.assertEqual(self.git("status", "--porcelain"), "")
            self.assertEqual((await self.call(client, "diff_platform"))["count"], 0)
            # Checking out never overwrites uncommitted changes.
            (self.folder / "parameters.json").write_text("{}")
            with self.assertRaisesRegex(ToolError, "uncommitted changes"):
                await self.call(client, "checkout_platform", ref="main")

    def test_pull_request_links_for_github_remotes(self):
        for url in (
            "git@github.com:qiboteam/qibolab_platforms_qrc.git",
            "https://github.com/qiboteam/qibolab_platforms_qrc",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    versions.GITHUB.search(url)["repository"], "qiboteam/qibolab_platforms_qrc",
                )
        self.assertIsNone(versions.GITHUB.search("/shared/platforms.git"))

    async def test_the_gateway_runs_version_tools_in_local_workers(self):
        commands = []

        def start(*command, **kwargs):
            commands.append(command)
            listing = {"session_base": None, "checkout": {}, "versions": [], "branches": []}
            result = CallToolResult(content=[], structuredContent=listing)
            return piped(SimpleNamespace(returncode=0, communicate=AsyncMock(
                return_value=(result.model_dump_json(by_alias=True).encode(), b""),
            )))

        with patch("asyncio.create_subprocess_exec", side_effect=start):
            gateway = create_slurm_server(
                "synthetic", partition="qpu", output=self.output,
                python=sys.executable, workdir=self.output.parent, slurm_args=[],
            )
            async with Client(gateway) as client:
                self.assertEqual((await self.call(client, "list_versions"))["versions"], [])
        self.assertNotEqual(commands[0][0], "srun")


if __name__ == "__main__":
    unittest.main()
