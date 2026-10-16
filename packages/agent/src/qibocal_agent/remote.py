"""Serve the calibration MCP over stdio from anywhere, for interactive clients.

Register this command in any MCP client configuration. It resolves the same
deployment settings as the agent (configuration file, named agents and CLI
options), then replaces itself with the server launch: directly on this
machine, or over SSH to the access node, provisioning a managed environment
there when required. The MCP conversation itself is carried by stdio.
"""

import argparse
import os

from .config import ConfigError, parse_settings
from .deployment import DeploymentSettings, add_agent_deployment_arguments, launch_for


def main(argv: list[str] | None = None) -> None:
    """Resolve settings and exec the stdio server launch."""
    parser = argparse.ArgumentParser(
        description=__doc__, argument_default=argparse.SUPPRESS
    )
    parser.add_argument(
        "agent_name", nargs="?", default=argparse.SUPPRESS,
        help="Configured agent whose deployment settings should be used",
    )
    add_agent_deployment_arguments(parser)
    args = parse_settings(parser, "agent", argv, require_model=False)
    try:
        launch = launch_for(DeploymentSettings.from_namespace(args))
        if launch.cwd is not None:
            os.chdir(launch.cwd)
        os.execvpe(
            launch.command,
            [launch.command, *launch.args],
            {**os.environ, **launch.env},
        )
    except (ConfigError, OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
