"""A model-directed calibration loop, without a prescribed strategy."""

import argparse
import asyncio
import os
import shlex
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset

from .config import parse_settings
from .models import resolve_model


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
Queue waits are normal. Do not blindly retry failed acquisitions or updates:
they may already have affected hardware. Stop when recovery needs user input.
"""


def transport_for(args: argparse.Namespace) -> StdioTransport:
    """Use stdio locally or carry it over SSH to an access-node gateway."""
    python = args.server_python or ("python3" if args.ssh else sys.executable)
    command = [
        python,
        "-m", "qibocal_mcp.server",
        "--no-config",
        "--platform", args.platform,
        "--output", args.output,
    ]
    if args.platforms is not None:
        command += ["--platforms", args.platforms]
    if args.partition is not None:
        command += ["--partition", args.partition]
    command += [f"--slurm-arg={arg}" for arg in args.slurm_arg]
    if args.ssh:
        remote = "exec " + shlex.join(command)
        if args.workdir is not None:
            remote = f"cd {shlex.quote(args.workdir)} && {remote}"
        return StdioTransport(
            command="ssh",
            args=["-T", "-o", "BatchMode=yes", "--", args.ssh, remote],
            env={
                name: os.environ[name]
                for name in ("SSH_AUTH_SOCK",)
                if name in os.environ
            },
            keep_alive=False,
        )
    return StdioTransport(
        command=python,
        args=command[1:],
        # StdioTransport otherwise drops platform/driver environment settings.
        env=dict(os.environ),
        cwd=args.workdir,
        keep_alive=False,
    )


async def run(args):
    """Keep one direct server or gateway alive for the entire calibration."""
    model = resolve_model(args.model, base_url=args.model_base_url)
    async with Client(
        transport_for(args), timeout=args.timeout, init_timeout=args.timeout
    ) as client:
        context = await client.read_resource("qibocal://context")
        agent = Agent(
            model,
            instructions=INSTRUCTIONS + "\nQibocal context:\n" + context[0].text,
            toolsets=[MCPToolset(client)],
        )
        async with agent:
            result = await agent.run(args.objective)
        print(result.output)


def main(argv: list[str] | None = None):
    """Run a calibration objective with a vision-capable model."""
    parser = argparse.ArgumentParser(
        description=__doc__, argument_default=argparse.SUPPRESS
    )
    parser.add_argument("objective", help="Calibration goal, limits and stopping criteria")
    parser.add_argument(
        "--model",
        help="Pydantic AI model identifier, github-copilot:ID or openwebui:ID",
    )
    parser.add_argument(
        "--model-base-url", help="OpenWebUI API base URL or Copilot endpoint override"
    )
    parser.add_argument("--platform")
    parser.add_argument("--output")
    parser.add_argument("--ssh", help="SSH host/alias for the access node")
    parser.add_argument("--partition", help="New SLURM job for each protocol/update")
    parser.add_argument(
        "--server-python", help="Server Python path, on the SSH host if remote"
    )
    parser.add_argument("--workdir", help="Working directory on the server/compute nodes")
    parser.add_argument("--platforms", help="QIBOLAB_PLATFORMS on the server/compute nodes")
    parser.add_argument("--slurm-arg", action="append", help="Extra srun option")
    parser.add_argument(
        "--timeout", type=float,
        help="Seconds per operation, including queue wait",
    )
    args = parse_settings(parser, "agent", argv)
    try:
        asyncio.run(run(args))
    except UserError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
