"""Serve the calibration MCP over stdio from anywhere, for interactive clients.

Register this command in any MCP client configuration. It resolves the same
deployment settings as the agent (configuration file, named agents and CLI
options) and launches the server: directly on this machine, or over SSH to
the access node, provisioning a managed environment there when required.

It stays in between as a thin proxy, forwarding every server tool, prompt and
resource, so the conversation can switch to another stored configuration with
the switch_configuration tool, without restarting the client.
"""

import argparse
import asyncio
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server.providers.proxy import ProxyProvider
from mcp.types import (
    PromptListChangedNotification, ResourceListChangedNotification,
    ToolListChangedNotification,
)

from .config import ConfigError, load_config, parse_settings
from .deployment import DeploymentSettings, add_agent_deployment_arguments, launch_for
from .environment import DeploymentError

INSTRUCTIONS = (
    "Calibration tools are forwarded from the Qibocal server of the active "
    "configuration. list_configurations shows the stored configurations and "
    "which one is active; switch_configuration replaces the server with another "
    "one. Switching closes the previous server: its run handles expire and "
    "unfinished work there is abandoned. Read the calibration context again "
    "after switching."
)


class _Parser(argparse.ArgumentParser):
    """Report invalid settings to the caller instead of exiting the proxy."""

    def error(self, message):
        raise ConfigError(message)


def _parser(cls: type[argparse.ArgumentParser] = argparse.ArgumentParser):
    parser = cls(description=__doc__, argument_default=argparse.SUPPRESS)
    parser.add_argument(
        "agent_name", nargs="?", default=argparse.SUPPRESS,
        help="Configured agent whose deployment settings should be used",
    )
    add_agent_deployment_arguments(parser)
    return parser


def resolve(argv: list[str]) -> DeploymentSettings:
    """Resolve one configuration, raising ConfigError rather than exiting."""
    args = parse_settings(_parser(_Parser), "agent", argv, require_model=False)
    return DeploymentSettings.from_namespace(args)


def summary(settings: DeploymentSettings) -> dict:
    return {
        "platform": settings.platform,
        "ssh": settings.ssh,
        "partition": settings.partition,
        "output": settings.output,
    }


class Upstream:
    """Own exactly one connected server; replace it on request."""

    def __init__(self, config: Path | None, enabled: bool):
        self.config = config
        self.enabled = enabled
        self.name: str | None = None
        self.settings: DeploymentSettings | None = None
        self.error: str | None = None
        self.client: Client | None = None
        self.stack: AsyncExitStack | None = None
        self.lock = asyncio.Lock()
        # Listing an unavailable server yields no tools, keeping local tools usable.
        self.empty = Client(FastMCP("Qibocal (no active configuration)"))

    def current(self) -> Client:
        return self.client if self.client is not None else self.empty

    def selection(self, name: str | None) -> list[str]:
        if not self.enabled:
            raise ConfigError("Configuration files are disabled (--no-config)")
        argv = [] if self.config is None else ["--config", str(self.config)]
        return argv + ([] if name is None else [name])

    async def close(self) -> None:
        if self.stack is not None:
            stack, self.stack, self.client = self.stack, None, None
            await stack.aclose()

    async def connect(self, name: str | None, settings: DeploymentSettings) -> None:
        """Close the active server first: both may need the same instruments."""
        await self.close()
        self.name, self.settings, self.error = name, settings, None
        stack = AsyncExitStack()
        try:
            launch = launch_for(settings)
            client = Client(
                StdioTransport(
                    command=launch.command, args=launch.args, env=launch.env,
                    cwd=launch.cwd, keep_alive=False,
                ),
                timeout=settings.timeout, init_timeout=settings.timeout,
            )
            await stack.enter_async_context(client)
        except BaseException as error:
            await stack.aclose()
            self.error = f"{type(error).__name__}: {error}"
            raise
        self.client, self.stack = client, stack


def create_proxy(upstream: Upstream, initial: DeploymentSettings, name: str | None) -> FastMCP:
    @asynccontextmanager
    async def lifespan(server):
        try:
            await upstream.connect(name, initial)
        except Exception as error:
            # Stay available, so the failure can be read and another configuration chosen.
            print(f"Could not start the Qibocal server: {error}", file=sys.stderr)
        try:
            yield {}
        finally:
            await upstream.close()

    mcp = FastMCP("Qibocal", instructions=INSTRUCTIONS, lifespan=lifespan)
    mcp.add_provider(ProxyProvider(upstream.current, cache_ttl=0))

    @mcp.tool
    async def list_configurations() -> dict:
        """List the stored calibration configurations and show the active one.

        name null denotes the unnamed default configuration. Each entry shows
        platform, ssh host, SLURM partition and output directory, or the
        reason it cannot be used.
        """
        configurations = []
        if upstream.enabled:
            data = load_config(upstream.config, missing_ok=upstream.config is None)
            names = [None, *data.get("agents", {})]
            for candidate in names:
                entry = {"name": candidate}
                try:
                    entry |= summary(resolve(upstream.selection(candidate)))
                except ConfigError as error:
                    if candidate is None and "agent" not in data:
                        continue
                    entry["error"] = str(error)
                configurations.append(entry)
        return {
            "active": {
                "name": upstream.name,
                "connected": upstream.client is not None,
                "error": upstream.error,
                **(summary(upstream.settings) if upstream.settings else {}),
            },
            "configurations": configurations,
        }

    @mcp.tool
    async def switch_configuration(ctx: Context, name: str | None = None) -> dict:
        """Replace the active Qibocal server with another stored configuration.

        name selects a named configuration from list_configurations; null
        selects the unnamed default. The previous server is closed first, so
        its run handles expire and any unfinished operation there is abandoned:
        finish or record work before switching. A first launch may provision
        an environment and take minutes. Afterwards, read the calibration
        context again; earlier session IDs and report paths no longer apply.
        """
        async with upstream.lock:
            settings = resolve(upstream.selection(name))
            try:
                await upstream.connect(name, settings)
            except (DeploymentError, OSError, RuntimeError, ValueError) as error:
                raise ValueError(
                    f"Could not start configuration {name!r}: {error}. "
                    "No configuration is active; switch again to recover."
                ) from error
        for notification in (
            ToolListChangedNotification(), PromptListChangedNotification(),
            ResourceListChangedNotification(),
        ):
            await ctx.send_notification(notification)
        return {"active": name, **summary(settings)}

    return mcp


def main(argv: list[str] | None = None) -> None:
    """Resolve the initial configuration and serve the switching proxy."""
    parser = _parser()
    args = parse_settings(parser, "agent", argv, require_model=False)
    try:
        initial = DeploymentSettings.from_namespace(args)
    except ConfigError as error:
        parser.error(str(error))
    upstream = Upstream(
        getattr(args, "config", None), not getattr(args, "no_config", False),
    )
    proxy = create_proxy(upstream, initial, getattr(args, "agent_name", None))
    proxy.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
