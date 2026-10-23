"""Serve the calibration MCP over stdio from anywhere, for interactive clients.

``qibocal-agent mcp`` resolves the same deployment settings as the agent
(configuration file, named agents and CLI options) and launches the server:
directly on this machine, or over SSH to the access node, provisioning a
managed environment there when required.

It stays in between as a thin proxy, forwarding every server tool, prompt and
resource, so the conversation can switch to another stored configuration with
the switch_configuration tool, without restarting the client, and serve the
reports of the active one to the browser with the open_reports tool.

Without a configuration named, it starts idle: nothing is launched, and the
calibration tools appear once the assistant selects a configuration — one
registration serves them all, and nothing happens until it is used.
"""

import asyncio
import importlib
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers.proxy import ProxyProvider
from mcp.types import (
    PromptListChangedNotification,
    ResourceListChangedNotification,
    ToolListChangedNotification,
)

from .config import DEFAULT, REPORT_ENVIRONMENT, ConfigError, configured_names, resolve
from .deployment import DeploymentSettings, launch_for
from .environment import DeploymentError, server_module
from .report import ReportServer, ReportSettings

INSTRUCTIONS = (
    "Calibration tools are forwarded from the Qibocal server of the active "
    "configuration. list_configurations shows the stored configurations and the "
    "active one; switch_configuration starts another, closing the previous "
    "server, its run handles and unfinished work. Without an active "
    "configuration, the calibration tools fail: ask the user which one to use. "
    "After switching, read the calibration context again. Before the first "
    "experiment of a configuration, call open_reports and give the user its "
    "link, which opens the reports with interactive plots in the browser."
)
INACTIVE = (
    "No Qibocal configuration is active: list them with list_configurations "
    "and select one with switch_configuration, then retry."
)
# Served even without a server: it only returns the calibration instructions.
STATIC_TOOL = "calibrate"
# The hosted Qibocamp interface, opening the server given as a query.
DASHBOARD = "https://qibo.science/qibocamp/"


class Inactive(Middleware):
    """Answer calls needing a server until a configuration is selected."""

    async def on_call_tool(self, context, call_next):
        if context.message.name != STATIC_TOOL:
            raise ToolError(INACTIVE)
        return await call_next(context)

    async def on_read_resource(self, context, call_next):
        # Interactive views are static pages, which hosts may load in advance.
        if str(context.message.uri).startswith("ui://"):
            return await call_next(context)
        raise ResourceError(INACTIVE)


def server_package(name: str):
    """A module of the server's frontend, which imports no Qibocal or hardware code.

    Its source is the one deployment ships: installed, in the checkout, or
    fetched from the commit qibocal-agent was installed from.
    """
    module = server_module()
    try:
        return importlib.import_module(f"qibocal_mcp.{name}")
    except ImportError:
        sys.path.insert(0, str(module.parent))
        return importlib.import_module(f"qibocal_mcp.{name}")


def server_definitions() -> FastMCP:
    """The server's frontend, for its tool definitions; nothing is started."""
    return server_package("server").create_server("inactive")


class ShowPlots(Middleware):
    """Word the plot reminder for the user's client, as the servers do for theirs.

    The proxy faces the user's interface, so its version is the one that counts.
    The server's middleware is loaded on first use; without it, the reminder
    from the server stays.
    """

    def __init__(self):
        self.delegate: Middleware | None = None

    async def on_call_tool(self, context, call_next):
        if self.delegate is None:
            try:
                self.delegate = server_package("plots").ShowPlots()
            except Exception as error:
                print(f"Plot reminders are left to the server: {error}", file=sys.stderr)
                self.delegate = Middleware()
        return await self.delegate.on_call_tool(context, call_next)


async def placeholder() -> FastMCP:
    """Advertise the server's tools while idle, for clients that list them once.

    Some clients keep the tools listed when they connect, and ignore later
    list changes: without these definitions, they would never see the
    calibration tools after a switch.
    """
    server = FastMCP("Qibocal (no active configuration)")
    try:
        definitions = server_definitions()
        tools = await definitions.list_tools()
        prompts = await definitions.list_prompts()
        resources = await definitions.list_resources()
    except Exception as error:
        print(
            f"The calibration tools will only be listed after a switch: {error}",
            file=sys.stderr,
        )
        return server
    for tool in tools:
        server.add_tool(tool)
    for prompt in prompts:
        server.add_prompt(prompt)
    for resource in resources:
        server.add_resource(resource)
    server.add_middleware(Inactive())
    return server


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
        # Stands in for the server while none is active; built on first use.
        self.empty: Client | None = None
        # The report server of the active configuration, once requested.
        self.reports: ReportServer | None = None

    async def current(self) -> Client:
        if self.client is not None:
            return self.client
        if self.empty is None:
            self.empty = Client(await placeholder())
        return self.empty

    def settings_for(self, name: str) -> DeploymentSettings:
        """Resolve a stored configuration, reading the file again each time."""
        if not self.enabled:
            raise ConfigError("Configuration files are disabled (--no-config)")
        return DeploymentSettings.from_values(resolve(
            "agent", {}, None if name == DEFAULT else name, config=self.config,
        ))

    def report_settings(self) -> ReportSettings:
        """Serve where the active server writes, with its stored report settings."""
        if self.settings is None:
            raise ConfigError(
                "No configuration is active: select one with switch_configuration"
            )
        stored = {}
        if self.enabled:
            stored = resolve(
                "report", {}, None if self.name in (None, DEFAULT) else self.name,
                config=self.config,
            )
        settings = self.settings
        port = stored.get("port", 8000)
        return ReportSettings(
            output=settings.output, ssh=settings.ssh, workdir=settings.workdir,
            directories=settings.directories, port=port,
            venv=stored.get("venv", REPORT_ENVIRONMENT),
            remote_port=stored.get("remote_port", port) if settings.ssh else port,
        )

    async def open_reports(self) -> ReportServer:
        """Start the report server, or reuse it while it runs."""
        if self.reports is not None and self.reports.running():
            return self.reports
        # Owned before it starts, so that closing meanwhile stops it.
        reports = self.reports = ReportServer(self.report_settings())
        try:
            if not await asyncio.to_thread(reports.start):
                raise DeploymentError("The report server exited while starting")
        except BaseException:
            if self.reports is reports:
                self.reports = None
            raise
        return reports

    async def close(self) -> None:
        if self.reports is not None:
            reports, self.reports = self.reports, None
            await asyncio.to_thread(reports.stop)
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


def create_proxy(
    upstream: Upstream, initial: DeploymentSettings | None = None, name: str | None = None,
) -> FastMCP:
    """The proxy, started on `initial`, or idle until a configuration is selected."""

    @asynccontextmanager
    async def lifespan(server):
        try:
            if initial is not None:
                await upstream.connect(name, initial)
        except Exception as error:
            # Stay available, so the failure can be read and another configuration chosen.
            print(f"Could not start the Qibocal server: {error}", file=sys.stderr)
        try:
            yield {}
        finally:
            await upstream.close()

    mcp = FastMCP("Qibocal", instructions=INSTRUCTIONS, lifespan=lifespan)
    mcp.add_middleware(ShowPlots())
    mcp.add_provider(ProxyProvider(upstream.current, cache_ttl=0))

    @mcp.tool
    async def list_configurations() -> dict:
        """List the stored calibration configurations and show the active one.

        default is the configuration used when no name is given. Each entry
        shows platform, ssh host, SLURM partition and output directory, or the
        reason it cannot be used.
        """
        configurations = []
        for candidate in configured_names(upstream.config, enabled=upstream.enabled):
            entry = {"name": candidate}
            try:
                entry |= summary(upstream.settings_for(candidate))
            except ConfigError as error:
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
    async def switch_configuration(ctx: Context, name: str = DEFAULT) -> dict:
        """Replace the active Qibocal server with another stored configuration.

        name selects a configuration from list_configurations; omit it for the
        default one. The previous server is closed first, so
        its run handles expire and any unfinished operation there is abandoned:
        finish or record work before switching. A first launch may provision
        an environment and take minutes. Afterwards, read the calibration
        context again; earlier session IDs and report paths no longer apply.
        """
        async with upstream.lock:
            settings = upstream.settings_for(name)
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

    @mcp.tool
    async def open_reports(session_id: str | None = None) -> dict:
        """Serve the calibration reports of the active configuration to the user.

        Starts a Qibocamp server where the reports are written, through
        an SSH tunnel when they are remote, and keeps it until the
        configuration changes. A first remote launch may install it and take
        minutes. Call it before the first experiment, and give the user the
        returned dashboard link: it opens the reports in their browser, with
        interactive plots. With session_id,
        from the calibration context, report_link also opens that session's
        report, once the dashboard has connected.
        """
        async with upstream.lock:
            try:
                reports = await upstream.open_reports()
            except ConfigError as error:
                raise ToolError(str(error)) from error
            except DeploymentError as error:
                raise ToolError(f"Could not serve the reports: {error}") from error
        result = {
            "dashboard": f"{DASHBOARD}#/connect?server={quote(reports.url, safe='')}",
            "server": reports.url,
            "output": reports.settings.output,
        }
        if session_id is not None:
            result["report_id"] = f"sessions/{session_id}"
            result["report_link"] = f"{DASHBOARD}#/reports/sessions/{session_id}"
        return result

    return mcp
