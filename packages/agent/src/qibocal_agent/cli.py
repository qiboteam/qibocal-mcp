"""The qibocal-agent command line: one command, one subcommand per task."""

import json
from dataclasses import dataclass
from pathlib import Path

import click
from click.core import ParameterSource

from .config import (
    ConfigError,
    configured_names,
    edit,
    get_config_file,
    load_config,
    resolve,
)
from .environment import DeploymentError


@dataclass(frozen=True)
class Selection:
    """The configuration file chosen with the group options."""

    config: Path | None
    enabled: bool

    @property
    def path(self) -> Path:
        return self.config if self.config is not None else get_config_file()


def explicit(ctx: click.Context, names: tuple[str, ...]) -> dict:
    """Options given on the command line, which override stored settings."""
    values = {}
    for name in names:
        if ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE:
            value = ctx.params[name]
            values[name] = list(value) if isinstance(value, tuple) else value
    return values


def settings(ctx: click.Context, section: str, name: str | None, names, **kwargs) -> dict:
    selection = ctx.find_object(Selection)
    try:
        return resolve(
            section, explicit(ctx, names), name,
            config=selection.config, enabled=selection.enabled, **kwargs,
        )
    except ConfigError as error:
        raise click.UsageError(str(error)) from error


DEPLOYMENT_OPTIONS = (
    click.option("--platform", help="Qibolab platform name."),
    click.option(
        "--platforms-dir",
        help="Folder with the platform definitions on the execution host "
        "(QIBOLAB_PLATFORMS); ~ is expanded there.",
    ),
    click.option("--ssh", help="SSH host or alias of the access node."),
    click.option("--partition", help="SLURM partition: one job per operation."),
    click.option(
        "--output",
        help="Report folder on the execution host "
        "(default: ~/.local/share/qibocal/agents/runs/NAME).",
    ),
    click.option(
        "--workdir", help="Working directory of the server and its SLURM jobs."
    ),
    click.option(
        "--venv",
        help="Managed or existing environment on the execution host "
        "(default: ~/.cache/qibocal/agents/envs/NAME).",
    ),
    click.option(
        "--server-python",
        help="Existing interpreter that already contains the server, instead of "
        "a managed environment.",
    ),
    click.option(
        "--dependency", multiple=True,
        help="Python requirement installed with the server (repeatable).",
    ),
    click.option(
        "--platform-repo", multiple=True,
        help="Git repository of platforms, optionally "
        "#ref=REV&subdirectory=PATH (repeatable).",
    ),
    click.option(
        "--slurm-arg", multiple=True, help="Extra srun option (repeatable)."
    ),
    click.option(
        "--timeout", type=float,
        help="Seconds per operation, including queue waits (default: 3600).",
    ),
)
DEPLOYMENT_NAMES = (
    "platform", "platforms_dir", "ssh", "partition", "output", "workdir", "venv",
    "server_python", "dependency", "platform_repo", "slurm_arg", "timeout",
)


def deployment_options(function):
    for option in reversed(DEPLOYMENT_OPTIONS):
        function = option(function)
    return function


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="qibocal-agent", prog_name="qibocal-agent")
@click.option(
    "--config", type=click.Path(path_type=Path, dir_okay=False),
    help="Use this configuration file instead of ~/.config/qibocal/agents.json.",
)
@click.option("--no-config", is_flag=True, help="Ignore configuration files.")
@click.pass_context
def main(ctx: click.Context, config: Path | None, no_config: bool) -> None:
    """Calibrate Qibocal platforms with AI agents, from wherever you are.

    Every command takes an optional configuration NAME, selecting a stored
    configuration ('default' when omitted). Options given on the command
    line override the stored settings.
    """
    if config is not None and no_config:
        raise click.UsageError("--config cannot be combined with --no-config")
    ctx.obj = Selection(config, not no_config)


@main.command(epilog="""\b
Examples:
  qibocal-agent run "Calibrate qubit 0's drive frequency, at most 100 shots."
  qibocal-agent run platinum "Inspect only; stop on errors."
""")
@click.argument("name", required=False)
@click.argument("objective", required=False)
@click.option(
    "--model",
    help="Pydantic AI model, e.g. openai:gpt-4.1, github-copilot:ID or openwebui:ID.",
)
@click.option(
    "--model-base-url", help="Endpoint for openwebui: or github-copilot: models."
)
@deployment_options
@click.pass_context
def run(ctx: click.Context, name: str | None, objective: str | None, **_) -> None:
    """Calibrate autonomously towards a bounded OBJECTIVE.

    The objective states what to calibrate, the hardware limits, a budget and
    when to stop; it may also be stored as NAME.objective. A single argument
    is a configuration name if one is stored with it, and the objective for
    the default configuration otherwise.
    """
    selection = ctx.find_object(Selection)
    if name is not None and objective is None:
        try:
            names = configured_names(selection.config, enabled=selection.enabled)
        except ConfigError as error:
            raise click.UsageError(str(error)) from error
        if name not in names:
            name, objective = None, name
    values = settings(
        ctx, "agent", name, (*DEPLOYMENT_NAMES, "model", "model_base_url"),
        require_model=True,
    )
    if objective is not None:
        values["objective"] = objective
    if values["objective"] is None:
        raise click.UsageError(
            f"An objective is required (pass one or set {values['name']}.objective)"
        )
    import asyncio

    from pydantic_ai.exceptions import UserError

    from .agent import run as run_agent

    try:
        asyncio.run(run_agent(values))
    except (UserError, DeploymentError) as error:
        raise click.ClickException(str(error)) from error


@main.command(epilog="""\b
Register it once in an MCP client, for example:
  {"mcpServers": {"qibocal": {"command": "/path/to/qibocal-agent",
                              "args": ["mcp"]}}}
""")
@click.argument("name", required=False)
@deployment_options
@click.pass_context
def mcp(ctx: click.Context, name: str | None, **_) -> None:
    """Serve the calibration MCP over stdio, for interactive assistants.

    With NAME, or settings on the command line, launches that Qibocal server,
    locally or through SSH, preparing its environment when needed, and
    forwards its tools. Without either, starts idle: the calibration tools are
    listed, but nothing is launched until the assistant selects a stored
    configuration, so one registration serves them all. Configurations are
    listed and switched within the conversation.
    """
    selection = ctx.find_object(Selection)
    from .remote import Upstream, create_proxy

    upstream = Upstream(selection.config, selection.enabled)
    if name is None and selection.enabled and not explicit(ctx, DEPLOYMENT_NAMES):
        # Idle, but on a file that can be read: a broken one is said at once.
        try:
            configured_names(selection.config, enabled=True)
        except ConfigError as error:
            raise click.UsageError(str(error)) from error
        create_proxy(upstream).run(transport="stdio", show_banner=False)
        return
    values = settings(ctx, "agent", name, DEPLOYMENT_NAMES)
    from .deployment import DeploymentSettings

    try:
        initial = DeploymentSettings.from_values(values)
    except ConfigError as error:
        raise click.UsageError(str(error)) from error
    create_proxy(upstream, initial, values["name"]).run(
        transport="stdio", show_banner=False,
    )


@main.command()
@click.argument("name", required=False)
@click.option("--output", help="Report folder to serve on the report host.")
@click.option("--ssh", help="SSH host or alias holding the reports.")
@click.option("--workdir", help="Working directory on the report host.")
@click.option(
    "--venv",
    help="Environment for the report server on the report host "
    "(default: qibocal-report).",
)
@click.option(
    "--port", type=int, help="Local port of the printed address (default: 8000)."
)
@click.option(
    "--remote-port", type=int,
    help="Port on the report host, through SSH (default: --port).",
)
@click.pass_context
def report(ctx: click.Context, name: str | None, **_) -> None:
    """Serve the calibration reports of configuration NAME.

    Starts a Qibocal Report backend where the reports are, through a private
    SSH tunnel when they are remote, and prints the address to register in
    the Qibocal Report interface. Stop it with Ctrl+C.
    """
    values = settings(
        ctx, "report", name,
        ("output", "ssh", "workdir", "venv", "port", "remote_port"),
    )
    from .report import ReportSettings
    from .report import run as run_report

    try:
        run_report(ReportSettings.from_values(values))
    except (ConfigError, DeploymentError) as error:
        raise click.ClickException(str(error)) from error


@main.group(invoke_without_command=True, epilog="""\b
Examples:
  qibocal-agent config set default.platform qutrits
  qibocal-agent config set platinum.dependency '["qibolab-qblox==0.0.4"]'
  qibocal-agent config get default.platform
""")
@click.pass_context
def config(ctx: click.Context) -> None:
    """Show or edit the stored configurations.

    Without a subcommand, prints the location of the configuration file and
    its content. Settings are addressed as NAME.setting, or
    NAME.report.setting for the report server.
    """
    selection = ctx.find_object(Selection)
    if not selection.enabled:
        raise click.UsageError("config cannot be combined with --no-config")
    if ctx.invoked_subcommand is None:
        data = _load(selection)
        click.echo(f"Configuration file: {selection.path.absolute()}")
        click.echo(json.dumps(data, indent=2))


def _load(selection: Selection) -> dict:
    try:
        return load_config(selection.config, missing_ok=selection.config is None)
    except ConfigError as error:
        raise click.UsageError(str(error)) from error


def _edit(ctx: click.Context, action: str, key: str, value: str | None = None):
    selection = ctx.find_object(Selection)
    try:
        return edit(selection.path, selection.config is not None, action, key, value)
    except (ConfigError, json.JSONDecodeError) as error:
        raise click.UsageError(str(error)) from error


@config.command()
@click.pass_context
def path(ctx: click.Context) -> None:
    """Print the location of the configuration file."""
    click.echo(ctx.find_object(Selection).path.absolute())


@config.command()
@click.pass_context
def show(ctx: click.Context) -> None:
    """Print the stored settings as JSON."""
    click.echo(json.dumps(_load(ctx.find_object(Selection)), indent=2))


@config.command()
@click.argument("key")
@click.pass_context
def get(ctx: click.Context, key: str) -> None:
    """Print one stored setting."""
    click.echo(_edit(ctx, "get", key))


@config.command(name="set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def set_(ctx: click.Context, key: str, value: str) -> None:
    """Store one setting.

    VALUE is taken literally for text settings, and as JSON for lists and
    numbers, e.g. '["pkg==1.0"]' or 7200.
    """
    _edit(ctx, "set", key, value)


@config.command()
@click.argument("key")
@click.pass_context
def unset(ctx: click.Context, key: str) -> None:
    """Remove one stored setting."""
    _edit(ctx, "unset", key)


if __name__ == "__main__":
    main()
