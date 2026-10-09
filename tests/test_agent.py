"""Check launch commands and SSH quoting without opening a network connection."""

import argparse
import os
import shlex
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pydantic_ai.exceptions import UserError

from qibocal_mcp.agent import run, transport_for


class TransportTests(unittest.TestCase):
    def options(self, **changes):
        options = dict(
            server_python=None, ssh=None, platform="qpu", output="runs",
            platforms=None, partition=None, workdir=None, slurm_arg=[],
        )
        options.update(changes)
        return argparse.Namespace(**options)

    def test_direct_compute_node(self):
        transport = transport_for(self.options())
        self.assertEqual(transport.command, sys.executable)
        self.assertIn("--no-config", transport.args)
        self.assertNotIn("--partition", transport.args)
        self.assertEqual(transport.env, dict(os.environ))
        self.assertFalse(transport.keep_alive)

    def test_access_node_dispatch(self):
        transport = transport_for(self.options(partition="qpu"))
        self.assertIn("--partition", transport.args)
        self.assertNotIn("ssh", transport.command)

    def test_ssh_to_gateway_quotes_remote_paths(self):
        directory = "/shared/calibration project"
        python = directory + "/.venv/bin/python"
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": "/tmp/test-ssh-agent"}):
            transport = transport_for(
                self.options(
                    ssh="access-node", partition="qpu", workdir=directory,
                    server_python=python, platforms="/shared/platform definitions",
                    slurm_arg=["--account=quantum"],
                )
            )
        self.assertEqual(transport.command, "ssh")
        self.assertEqual(
            transport.args[:5],
            ["-T", "-o", "BatchMode=yes", "--", "access-node"],
        )
        tokens = shlex.split(transport.args[-1])
        self.assertEqual(tokens[:4], ["cd", directory, "&&", "exec"])
        self.assertEqual(tokens[4], python)
        self.assertIn("--partition", tokens)
        self.assertIn("/shared/platform definitions", tokens)
        self.assertIn("--slurm-arg=--account=quantum", tokens)
        self.assertEqual(transport.env, {"SSH_AUTH_SOCK": "/tmp/test-ssh-agent"})
        self.assertFalse(transport.keep_alive)

    def test_workdir_is_not_resolved_twice(self):
        transport = transport_for(
            self.options(partition="qpu", workdir="relative/project")
        )
        self.assertEqual(transport.cwd, "relative/project")
        self.assertNotIn("--workdir", transport.args)


class AgentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_credentials_do_not_launch_hardware_server(self):
        args = argparse.Namespace(
            model="openwebui:model", model_base_url="http://localhost:3000/api"
        )
        with patch.dict(os.environ, {}, clear=True), patch("qibocal_mcp.agent.Client") as client:
            with self.assertRaisesRegex(UserError, "OPENWEBUI_API_KEY"):
                await run(args)
        client.assert_not_called()

    async def test_resolved_model_is_passed_to_calibration_agent(self):
        args = argparse.Namespace(
            model="openwebui:model", model_base_url="http://localhost:3000/api",
            timeout=60, objective="Inspect without acquiring",
        )
        client = AsyncMock()
        client.read_resource.return_value = [Mock(text="platform context")]
        calibration = AsyncMock()
        calibration.run.return_value = Mock(output="No updates.")
        model = Mock()
        with (
            patch("qibocal_mcp.agent.resolve_model", return_value=model) as resolve,
            patch("qibocal_mcp.agent.transport_for"),
            patch("qibocal_mcp.agent.Client") as create_client,
            patch("qibocal_mcp.agent.Agent", return_value=calibration) as create_agent,
            patch("qibocal_mcp.agent.MCPToolset") as toolset,
            patch("builtins.print") as output,
        ):
            create_client.return_value.__aenter__.return_value = client
            await run(args)
        resolve.assert_called_once_with(args.model, base_url=args.model_base_url)
        self.assertIs(create_agent.call_args.args[0], model)
        self.assertIn("platform context", create_agent.call_args.kwargs["instructions"])
        toolset.assert_called_once_with(client)
        calibration.run.assert_awaited_once_with(args.objective)
        output.assert_called_once_with("No updates.")
