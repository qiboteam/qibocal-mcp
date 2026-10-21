"""A model-directed calibration loop, without a prescribed strategy."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp.types import TextContent, TextResourceContents
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from .deployment import DeploymentSettings, launch_for
from .models import resolve_model

# The server's wire interface: its context resource and calibration prompt.
CONTEXT_RESOURCE = "qibocal://context"
CALIBRATION_PROMPT = "calibrate"


async def calibration_instructions(client: Client) -> str:
    """Compose the server-distributed calibration policy with its context."""
    prompt = await client.get_prompt(CALIBRATION_PROMPT)
    if len(prompt.messages) != 1 or not isinstance(
        prompt.messages[0].content, TextContent
    ):
        raise ValueError("Server returned an invalid calibration prompt")
    context = await client.read_resource(CONTEXT_RESOURCE)
    if len(context) != 1 or not isinstance(context[0], TextResourceContents):
        raise ValueError("Server returned an invalid context resource")
    return (
        prompt.messages[0].content.text + "\nQibocal context:\n" + context[0].text
    )


def transport_for(settings: Mapping[str, object] | DeploymentSettings) -> StdioTransport:
    """Adapt the shared launch contract to FastMCP's client transport."""
    if not isinstance(settings, DeploymentSettings):
        settings = DeploymentSettings.from_values(settings)
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
        agent = Agent(model, instructions=await calibration_instructions(client),
                      toolsets=[MCPToolset(client)])
        async with agent:
            yield agent


async def run(values: Mapping[str, object]) -> None:
    """Keep one direct server or gateway alive for the entire calibration."""
    model = resolve_model(values["model"], base_url=values.get("model_base_url"))
    async with open_calibration_agent(
        DeploymentSettings.from_values(values), model,
    ) as agent:
        result = await agent.run(values["objective"])
    print(result.output)
