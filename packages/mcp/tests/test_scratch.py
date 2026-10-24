"""Scratch protocols from local and pushed Git commits, with synthetic hardware."""

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from qibocal.calibration import CalibrationPlatform
from qibocal.calibration.calibration import Calibration, QubitCalibration
from qibolab import create_platform

from qibocal_mcp.runtime import create_server
from qibocal_mcp.scratch import PACKAGE, Scratch

IDENTITY = {
    "GIT_AUTHOR_NAME": "Tests", "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "Tests", "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}
# A complete protocol, as an agent would write it; FACTOR changes between commits.
PROTOCOL = textwrap.dedent("""\
    from dataclasses import dataclass

    import numpy as np
    from qibocal.auto.operation import Data, Parameters, Protocol, Results

    FACTOR = {factor}


    @dataclass
    class Inputs(Parameters):
        value: float


    @dataclass
    class Samples(Data):
        data: dict


    @dataclass
    class Fit(Results):
        frequency: dict


    def acquire(params: Inputs, targets) -> Samples:
        return Samples({{q: np.array([params.value * FACTOR]) for q in targets}})


    def fit(data: Samples) -> Fit:
        return Fit({{q: float(data[q][0]) for q in data.qubits}})


    def update(results, platform, target):
        platform.calibration.single_qubits[target].qubit.frequency_01 = (
            results.frequency[target]
        )


    variant = Protocol(acquire, fit, update=update)
""")


def make_platform(_=None):
    return CalibrationPlatform(
        **vars(create_platform("dummy")),
        calibration=Calibration(single_qubits={0: QubitCalibration()}),
    )


class ScratchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / "runs"
        self.repository = self.root / "scratch"
        self.repository.mkdir()
        self.enterContext(patch.dict(os.environ, IDENTITY))
        self.platforms = []

        def create(_):
            platform = make_platform()
            self.platforms.append(platform)
            return platform

        self.enterContext(patch("qibocal_mcp.runtime.create_calibration_platform", side_effect=create))
        self.git("init", "--quiet")
        (self.repository / "__init__.py").touch()
        (self.repository / ".gitignore").write_text("__pycache__/\n")
        self.addCleanup(lambda: [
            __import__("sys").modules.pop(name)
            for name in list(__import__("sys").modules) if name.split(".")[0] == PACKAGE
        ])

    def git(self, *arguments, cwd=None):
        return subprocess.run(
            ["git", "--no-pager", "-C", str(cwd or self.repository), *arguments],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, factor: int) -> str:
        (self.repository / "variant.py").write_text(PROTOCOL.format(factor=factor))
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", f"factor {factor}")
        return self.git("rev-parse", "HEAD")

    async def scratch_run(self, client, value=5e9, **arguments):
        return (await client.call_tool("execute_protocol", {
            "protocol": "variant.variant", "source": "scratch",
            "parameters": {"value": value}, "targets": [0], **arguments,
        })).structured_content

    async def test_local_runs_execute_the_committed_head(self):
        server = create_server("synthetic", output=self.output, scratch=Scratch(self.repository))
        async with Client(server) as client:
            context = json.loads((await client.call_tool("get_calibration_context", {})).content[0].text)
            self.assertIn(str(self.repository.resolve()), context["scratch"])
            # Before the first commit, the files are uncommitted changes.
            with self.assertRaisesRegex(ToolError, "uncommitted changes"):
                await self.scratch_run(client)
            first = self.commit(1)
            # Uncommitted changes are refused, untracked files included.
            (self.repository / "draft.py").write_text("x = 1\n")
            with self.assertRaisesRegex(ToolError, "uncommitted changes"):
                await self.scratch_run(client)
            (self.repository / "draft.py").unlink()
            result = await self.scratch_run(client)
            self.assertEqual(result["scratch"], {
                "source": "scratch", "protocol": "variant.variant",
                "commit": first, "path": "variant.py",
            })
            self.assertEqual(result["results"]['"frequency"'], {"0": 5e9})
            self.assertTrue(Path(result["folder"]).name.startswith("scratch.variant.variant-"))
            self.assertEqual(self.git("status", "--porcelain"), "")
            definitions = (await client.call_tool("get_protocol", {
                "name": "variant.variant", "source": "scratch",
            })).structured_content
            self.assertIn("value: float", definitions["parameters"])
            self.assertEqual(definitions["scratch"]["commit"], first)

            # A new commit replaces the imported code.
            second = self.commit(2)
            changed = await self.scratch_run(client)
            self.assertEqual(changed["scratch"]["commit"], second)
            self.assertEqual(changed["results"]['"frequency"'], {"0": 1e10})
            with self.assertRaisesRegex(ToolError, "not the repository's HEAD"):
                await self.scratch_run(client, scratch_commit=first)
            await client.call_tool("update_platform", {"run_id": result["run_id"]})
            self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 5e9)
            for name in ("missing.variant", "variant.missing", "variant"):
                with self.subTest(name=name), self.assertRaises(ToolError):
                    await self.scratch_run(client, protocol=name)
            # Built-in protocols are unaffected.
            with self.assertRaisesRegex(ToolError, "Unknown protocol"):
                await client.call_tool("execute_protocol", {
                    "protocol": "variant.variant", "parameters": {}, "targets": [0],
                })

        restored = create_server(
            "synthetic", output=self.output, scratch=Scratch(self.repository),
            restore_runs=True, session_id=result["session_id"],
        )
        async with Client(restored) as client:
            # Reports only know the protocol's name: it is loaded at its commit.
            await client.call_tool("update_platform", {"run_id": changed["run_id"]})
            self.assertEqual(self.platforms[-1].calibration.single_qubits[0].qubit.frequency_01, 1e10)
        restored = create_server(
            "synthetic", output=self.output, scratch=Scratch(self.repository),
            restore_runs=True, session_id=result["session_id"],
        )
        async with Client(restored) as client:
            with self.assertRaisesRegex(ToolError, "not the repository's HEAD"):
                await client.call_tool("update_platform", {"run_id": result["run_id"]})

    async def test_disabled_scratch_is_refused(self):
        async with Client(create_server("synthetic", output=self.output)) as client:
            context = json.loads((await client.call_tool("get_calibration_context", {})).content[0].text)
            self.assertNotIn("scratch", context)
            with self.assertRaisesRegex(ToolError, "not enabled"):
                await self.scratch_run(client)

    async def test_pushed_commits_run_in_one_checkout(self):
        bare, checkout = self.root / "remote.git", self.root / "remote"
        subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
        first = self.commit(1)
        self.git("push", "--quiet", "--force", str(bare), f"{first}:refs/qibocal/scratch")
        scratch = Scratch(checkout, bare)
        async with Client(create_server("synthetic", output=self.output, scratch=scratch)) as client:
            with self.assertRaisesRegex(ToolError, "pushed by the client"):
                await self.scratch_run(client)
            with self.assertRaisesRegex(ToolError, "Unknown scratch commit"):
                await self.scratch_run(client, scratch_commit="0" * 40)
            result = await self.scratch_run(client, scratch_commit=first)
            self.assertEqual(result["scratch"]["commit"], first)
            second = self.commit(2)
            self.git("push", "--quiet", "--force", str(bare), f"{second}:refs/qibocal/scratch")
            self.assertEqual(
                (await self.scratch_run(client, scratch_commit=second))["results"]['"frequency"'],
                {"0": 1e10},
            )
            # One working directory, exactly at the last commit.
            self.assertEqual(sorted(path.name for path in checkout.iterdir()), [
                ".gitignore", "__init__.py", "variant.py",
            ])
            self.assertIn("FACTOR = 2", (checkout / "variant.py").read_text())
        restored = create_server(
            "synthetic", output=self.output, scratch=Scratch(checkout, bare),
            restore_runs=True, session_id=result["session_id"],
        )
        async with Client(restored) as client:
            # An earlier commit is checked out again, while still available.
            await client.call_tool("update_platform", {"run_id": result["run_id"]})
            self.assertIn("FACTOR = 1", (checkout / "variant.py").read_text())


if __name__ == "__main__":
    unittest.main()
