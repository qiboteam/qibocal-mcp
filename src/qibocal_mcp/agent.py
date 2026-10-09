"""A model-directed calibration loop, without a prescribed strategy."""

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp.types import TextResourceContents
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from .calibration import CONTEXT_RESOURCE, INSTRUCTIONS, instructions_for
from .config import parse_settings
from .deployment import DeploymentSettings, add_agent_deployment_arguments, launch_for
from .environment import DeploymentError
from .models import resolve_model


def transport_for(args: argparse.Namespace | DeploymentSettings) -> StdioTransport:
    """Adapt the shared launch contract to FastMCP's client transport."""
    settings = (
        args if isinstance(args, DeploymentSettings)
        else DeploymentSettings.from_namespace(args)
    )
    if settings.report_rendering == "client":
        raise UserError("Client report rendering requires qibocal-copilot")
    launch = launch_for(settings)
    return StdioTransport(
        command=launch.command, args=launch.args, env=launch.env,
        cwd=launch.cwd, keep_alive=False,
    )


@asynccontextmanager
async def open_calibration_agent(
    settings: DeploymentSettings,
    model: Model | str,
    *,
    model_base_url: str | None = None,
) -> AsyncIterator[Agent]:
    """Yield a configured agent while keeping its MCP session alive.

    Run all turns inside this context. Model resolution precedes deployment
    and hardware access; explicit model objects retain their configuration.
    """
    if isinstance(model, str):
        model = resolve_model(model, base_url=model_base_url)
    elif model_base_url is not None:
        raise UserError("model_base_url cannot be combined with a model object")
    async with Client(
        transport_for(settings),
        timeout=settings.timeout, init_timeout=settings.timeout,
    ) as client:
        context = await client.read_resource(CONTEXT_RESOURCE)
        if len(context) != 1 or not isinstance(context[0], TextResourceContents):
            raise ValueError("Server returned an invalid context resource")
        agent = Agent(model, instructions=instructions_for(context[0].text),
                      toolsets=[MCPToolset(client)])
        async with agent:
            yield agent


async def run(args):
    """Keep one direct server or gateway alive for the entire calibration."""
    model = resolve_model(args.model, base_url=args.model_base_url)
    async with open_calibration_agent(
        DeploymentSettings.from_namespace(args), model,
    ) as agent:
        result = await agent.run(args.objective)
    print(result.output)


def main(argv: list[str] | None = None):
    """Run a calibration objective with a vision-capable model."""
    parser = argparse.ArgumentParser(
        description=__doc__, argument_default=argparse.SUPPRESS
    )
    parser.add_argument("objective", help="Calibration goal, limits and stopping criteria")
    parser.add_argument(
        "--model", help="Pydantic AI model identifier, github-copilot:ID or openwebui:ID",
    )
    parser.add_argument(
        "--model-base-url", help="OpenWebUI API base URL or Copilot endpoint override"
    )
    add_agent_deployment_arguments(parser)
    args = parse_settings(parser, "agent", argv)
    try:
        asyncio.run(run(args))
    except (UserError, DeploymentError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
