"""Check launch commands and SSH quoting without opening a network connection."""

import argparse
import json
import os
import shlex
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mcp.types import GetPromptResult, PromptMessage, TextContent, TextResourceContents
from pydantic_ai.exceptions import UserError

from qibocal_agent.agent import open_calibration_agent, run, transport_for
from qibocal_agent.deployment import DeploymentSettings

POLICY = "You are a quantum calibration agent (test policy)."


def server_prompt(text=POLICY):
    return GetPromptResult(messages=[
        PromptMessage(role="user", content=TextContent(type="text", text=text))
    ])


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
        self.assertEqual(transport.args[:2], ["-m", "qibocal_mcp.server"])
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

    def test_ssh_defaults_to_managed_environment(self):
        transport = transport_for(self.options(ssh="access-node", platforms="/explicit"))
        tokens = shlex.split(transport.args[-1])
        self.assertEqual(tokens[:3], ["exec", "python3", "-c"])
        request = json.loads(tokens[4])
        self.assertEqual(request["options"]["venv"], "qibocal-mcp")
        self.assertEqual(request["options"]["platforms"], "/explicit")
        self.assertIn("source", request)
        self.assertIn("--platform", tokens[5:])
        # The bootstrap sets the combined environment; the server must not replace it.
        self.assertNotIn("--platforms", tokens[5:])

    def test_local_managed_environment_and_dependency_options(self):
        transport = transport_for(self.options(
            venv="lab", platform_repo=["git@host:repo#subdirectory=platforms"],
            dependency=["driver>=1"], workdir="relative/project",
        ))
        self.assertEqual(transport.command, sys.executable)
        self.assertEqual(transport.args[0], "-c")
        request = json.loads(transport.args[2])
        self.assertEqual(request["options"]["venv"], "lab")
        self.assertEqual(request["options"]["platform_repo"], ["git@host:repo#subdirectory=platforms"])
        self.assertEqual(request["options"]["dependency"], ["driver>=1"])
        self.assertEqual(transport.cwd, "relative/project")

    def test_explicit_interpreter_provisions_repositories_without_core_install(self):
        transport = transport_for(self.options(
            ssh="access-node", server_python="/existing/python",
            platform_repo=["git@host:repo"], dependency=["driver"],
        ))
        tokens = shlex.split(transport.args[-1])
        request = json.loads(tokens[4])
        self.assertIsNone(request["options"]["venv"])
        self.assertNotIn("source", request)
        self.assertEqual(request["options"]["python"], "/existing/python")


class AgentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_credentials_do_not_launch_hardware_server(self):
        args = argparse.Namespace(
            model="openwebui:model", model_base_url="http://localhost:3000/api"
        )
        with patch.dict(os.environ, {}, clear=True), patch("qibocal_agent.agent.Client") as client:
            with self.assertRaisesRegex(UserError, "OPENWEBUI_API_KEY"):
                await run(args)
        client.assert_not_called()

    async def test_resolved_model_is_passed_to_calibration_agent(self):
        args = argparse.Namespace(
            model="openwebui:model", model_base_url="http://localhost:3000/api",
            platform="qpu", timeout=60, objective="Inspect without acquiring",
        )
        client = AsyncMock()
        client.get_prompt.return_value = server_prompt()
        client.read_resource.return_value = [
            TextResourceContents(uri="qibocal://context", text="platform context")
        ]
        calibration = AsyncMock()
        calibration.run.return_value = Mock(output="No updates.")
        model = Mock()
        with (
            patch("qibocal_agent.agent.resolve_model", return_value=model) as resolve,
            patch("qibocal_agent.agent.transport_for"),
            patch("qibocal_agent.agent.Client") as create_client,
            patch("qibocal_agent.agent.Agent", return_value=calibration) as create_agent,
            patch("qibocal_agent.agent.MCPToolset") as toolset,
            patch("builtins.print") as output,
        ):
            create_client.return_value.__aenter__.return_value = client
            await run(args)
        resolve.assert_called_once_with(args.model, base_url=args.model_base_url)
        self.assertIs(create_agent.call_args.args[0], model)
        self.assertIn("platform context", create_agent.call_args.kwargs["instructions"])
        self.assertTrue(create_agent.call_args.kwargs["instructions"].startswith(POLICY))
        client.get_prompt.assert_awaited_once_with("calibrate")
        toolset.assert_called_once_with(client)
        calibration.run.assert_awaited_once_with(args.objective)
        output.assert_called_once_with("No updates.")

    async def test_python_factory_keeps_client_alive_for_multiple_turns(self):
        client = AsyncMock()
        client.get_prompt.return_value = server_prompt()
        client.read_resource.return_value = [
            TextResourceContents(uri="qibocal://context", text="platform context")
        ]
        calibration = AsyncMock()
        model = Mock()
        settings = DeploymentSettings(platform="qpu", timeout=42)
        with (
            patch("qibocal_agent.agent.resolve_model") as resolve,
            patch("qibocal_agent.agent.transport_for") as transport,
            patch("qibocal_agent.agent.Client") as create_client,
            patch("qibocal_agent.agent.Agent", return_value=calibration) as create_agent,
            patch("qibocal_agent.agent.MCPToolset"),
        ):
            create_client.return_value.__aenter__.return_value = client
            async with open_calibration_agent(settings, model) as agent:
                self.assertIs(agent, calibration)
                await agent.run("first turn")
                await agent.run("second turn", message_history=[])
                create_client.return_value.__aexit__.assert_not_awaited()
            create_client.return_value.__aexit__.assert_awaited_once()
            calibration.__aexit__.assert_awaited_once()
        resolve.assert_not_called()
        self.assertIs(create_agent.call_args.args[0], model)
        self.assertEqual(create_client.call_args.kwargs["timeout"], 42)
        self.assertEqual(create_client.call_args.kwargs["init_timeout"], 42)
        transport.assert_called_once_with(settings)

    async def test_python_factory_resolves_identifier_before_launch(self):
        with (
            patch("qibocal_agent.agent.resolve_model", side_effect=UserError("bad model")),
            patch("qibocal_agent.agent.transport_for") as transport,
            patch("qibocal_agent.agent.Client") as client,
        ):
            with self.assertRaisesRegex(UserError, "bad model"):
                async with open_calibration_agent(
                    DeploymentSettings(platform="qpu"), "invalid:model",
                ):
                    self.fail("unreachable")
        transport.assert_not_called()
        client.assert_not_called()

    async def test_python_factory_rejects_endpoint_with_model_object(self):
        with patch("qibocal_agent.agent.Client") as client:
            with self.assertRaisesRegex(UserError, "model object"):
                async with open_calibration_agent(
                    DeploymentSettings(platform="qpu"), Mock(), model_base_url="https://example",
                ):
                    self.fail("unreachable")
        client.assert_not_called()

    async def test_invalid_prompt_closes_client_without_constructing_agent(self):
        client = AsyncMock()
        client.get_prompt.return_value = GetPromptResult(messages=[])
        with (
            patch("qibocal_agent.agent.transport_for"),
            patch("qibocal_agent.agent.Client") as create_client,
            patch("qibocal_agent.agent.Agent") as create_agent,
        ):
            create_client.return_value.__aenter__.return_value = client
            with self.assertRaisesRegex(ValueError, "invalid calibration prompt"):
                async with open_calibration_agent(DeploymentSettings(platform="qpu"), Mock()):
                    self.fail("unreachable")
            create_client.return_value.__aexit__.assert_awaited_once()
        create_agent.assert_not_called()

    async def test_invalid_context_closes_client_without_constructing_agent(self):
        client = AsyncMock()
        client.get_prompt.return_value = server_prompt()
        client.read_resource.return_value = []
        with (
            patch("qibocal_agent.agent.transport_for"),
            patch("qibocal_agent.agent.Client") as create_client,
            patch("qibocal_agent.agent.Agent") as create_agent,
        ):
            create_client.return_value.__aenter__.return_value = client
            with self.assertRaisesRegex(ValueError, "invalid context"):
                async with open_calibration_agent(DeploymentSettings(platform="qpu"), Mock()):
                    self.fail("unreachable")
            create_client.return_value.__aexit__.assert_awaited_once()
        create_agent.assert_not_called()
