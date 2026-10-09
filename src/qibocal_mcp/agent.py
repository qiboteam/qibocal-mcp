"""A model-directed calibration loop, without a prescribed strategy."""

import argparse
import asyncio
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset


INSTRUCTIONS = """You are a quantum calibration agent.
Use the user's objective and constraints to plan a calibration strategy.
Choose protocols and parameters from the supplied Qibocal context; respect its
units and qubit IDs. Explain your decisions, but do not invent execution results.
Execute one protocol at a time. Inspect its returned report images and fitting
tables: look for signal quality, model agreement, uncertainty, and bad fits.
Only call update_platform when the inspected evidence justifies the update.
If evidence is insufficient, leave the platform unchanged and adjust the next
experiment. An update affects subsequent acquisitions; old runs can be stale.
Adapt your plan after each result. Stop when the objective is met, the user's
budget is exhausted, or progress requires user input. Summarize what was run,
which updates were applied, artifact locations, and remaining uncertainty.
Follow the user's hardware limits. Do not treat synthetic data from a mock
platform as evidence of a successful calibration.
"""


async def run(args):
    """Keep a single MCP process alive for the entire calibration."""
    transport = StdioTransport(
        command=sys.executable,
        args=[
            "-m", "qibocal_mcp.server",
            "--platform", args.platform,
            "--output", args.output,
        ],
        # StdioTransport otherwise drops platform/driver environment settings.
        env=dict(os.environ),
    )
    async with Client(transport, timeout=3600) as client:
        context = await client.read_resource("qibocal://context")
        agent = Agent(
            args.model,
            instructions=INSTRUCTIONS + "\nQibocal context:\n" + context[0].text,
            toolsets=[MCPToolset(client)],
        )
        async with agent:
            result = await agent.run(args.objective)
        print(result.output)


def main():
    """Run a calibration objective with a vision-capable model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("objective", help="Calibration goal, limits and stopping criteria")
    parser.add_argument("--model", required=True, help="Pydantic AI model identifier")
    parser.add_argument("--platform", required=True)
    parser.add_argument("--output", default="runs")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
