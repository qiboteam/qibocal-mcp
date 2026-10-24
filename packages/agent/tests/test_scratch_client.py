"""Scratch settings, the client repository and its commits through the proxy."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from qibocal_agent import config, remote, scratch
from qibocal_agent.deployment import DeploymentSettings, launch_for

IDENTITY = {
    "GIT_AUTHOR_NAME": "Tests", "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "Tests", "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


class ScratchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.enterContext(patch.dict(os.environ, {
            **IDENTITY,
            "QIBOCAL_AGENTS_CONFIG_DIR": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"),
        }))
        self.repository = self.root / "data" / "qibocal" / "agents" / "scratch" / "lab"

    def git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def test_enabling_the_toggle_is_enough(self):
        config.save_config({"agents": {
            "lab": {"platform": "chip", "scratch": True},
            "cluster": {"platform": "chip", "ssh": "node", "scratch": True},
            "off": {"platform": "chip"},
        }})
        local = config.resolve("agent", {}, "lab")
        self.assertEqual(local["scratch_path"], str(self.repository))
        self.assertIsNone(local["scratch_remote"])
        cluster = config.resolve("agent", {}, "cluster")
        self.assertEqual(cluster["scratch_remote"], "~/.local/share/qibocal/agents/scratch/cluster.git")
        self.assertIsNone(config.resolve("agent", {}, "off")["scratch_path"])
        # The command line disables it for one launch.
        self.assertFalse(config.resolve("agent", {"scratch": False}, "lab")["scratch"])
        with self.assertRaisesRegex(config.ConfigError, "true or false"):
            config.validate_config({"agents": {"x": {"scratch": "yes"}}})

    def test_launches_pass_the_repository_or_its_remote_checkout(self):
        local = launch_for(DeploymentSettings(platform="chip", scratch=True, scratch_path="/repo"))
        self.assertEqual(local.args[-2:], ["--scratch", "/repo"])
        cluster = launch_for(DeploymentSettings(
            platform="chip", ssh="node", server_python="python3", scratch=True,
            scratch_path="/repo", scratch_remote="~/s/lab.git",
        ))
        command = shlex.split(cluster.args[-1])
        self.assertEqual(command[-4:], ["--scratch", "~/s/lab", "--scratch-git-dir", "~/s/lab.git"])
        self.assertNotIn("--scratch", launch_for(DeploymentSettings(platform="chip")).args)

    def test_the_repository_must_be_committed(self):
        with self.assertRaisesRegex(scratch.ScratchError, "uncommitted changes"):
            scratch.head(self.repository)
        # A managed repository is created on first use, as an empty package.
        self.assertEqual(
            sorted(path.name for path in self.repository.iterdir()),
            [".git", ".gitignore", "__init__.py"],
        )
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "start")
        commit = scratch.head(self.repository)
        self.assertEqual(commit, self.git("rev-parse", "HEAD"))
        # Bytecode caches are ignored, even without a .gitignore.
        (self.repository / ".gitignore").unlink()
        self.git("commit", "--quiet", "-am", "no ignore")
        (self.repository / "__pycache__").mkdir()
        (self.repository / "__pycache__" / "x.pyc").write_bytes(b"")
        self.assertEqual(scratch.head(self.repository), self.git("rev-parse", "HEAD"))

    def test_pushes_create_the_remote_and_update_one_ref(self):
        with (
            patch("qibocal_agent.scratch.subprocess.run", return_value=Mock(returncode=0)) as ssh,
            patch("qibocal_agent.scratch.git") as git,
        ):
            scratch.push(Path("/repo"), "node", "~/s/lab.git", "abc")
        self.assertEqual(ssh.call_args.args[0][-2:], ["node", "git init --quiet --bare s/lab.git"])
        git.assert_called_once_with(
            Path("/repo"), "push", "--quiet", "--force", "node:s/lab.git",
            "abc:refs/qibocal/scratch",
        )

    async def test_the_proxy_passes_the_committed_head(self):
        calls = []
        server = FastMCP("Fake")

        @server.tool
        def execute_protocol(
            protocol: str, source: str = "qibocal", scratch_commit: str | None = None,
        ) -> dict:
            calls.append((protocol, source, scratch_commit))
            return {}

        upstream = remote.Upstream(None, True)
        upstream.client = Client(server)
        upstream.settings = DeploymentSettings(
            platform="chip", scratch=True, scratch_path=str(self.repository),
        )
        proxy = remote.create_proxy(upstream)
        async with upstream.client, Client(proxy) as client:
            with self.assertRaisesRegex(ToolError, "uncommitted changes"):
                await client.call_tool("execute_protocol", {"protocol": "m.p", "source": "scratch"})
            self.git("add", ".")
            self.git("commit", "--quiet", "-m", "start")
            await client.call_tool("execute_protocol", {"protocol": "m.p", "source": "scratch"})
            await client.call_tool("execute_protocol", {"protocol": "rabi"})
        self.assertEqual(calls, [
            ("m.p", "scratch", self.git("rev-parse", "HEAD")), ("rabi", "qibocal", None),
        ])


if __name__ == "__main__":
    unittest.main()
