"""Launch or export a Copilot-native calibration profile."""

import argparse
import base64
import json
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .calibration import DESCRIPTION, NAME, TOOLS, instructions_for
from .config import ConfigError, parse_settings
from .deployment import DeploymentSettings, ServerLaunch, add_agent_deployment_arguments, launch_for
from .environment import DeploymentError


PROFILE_ID = f"{NAME}:{NAME}"
EXCLUDED_TOOLS = ("skill", "sql")


class CopilotError(RuntimeError):
    """An unavailable or incompatible Copilot CLI."""


def model_environment(name: str) -> bool:
    """Do not forward model-provider credentials to the hardware subprocess."""
    return (
        name in {"GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_KEY"}
        or (name.startswith("COPILOT_") and not name.startswith("COPILOT_MCP_"))
        or name.startswith((
            "GITHUB_COPILOT_", "OPENAI_", "ANTHROPIC_", "OPENWEBUI_",
            "GEMINI_", "GOOGLE_API_",
        ))
    )


def server_config(launch: ServerLaunch, timeout: float) -> dict:
    """Reference runtime environment without embedding its values in files."""
    if not 0.001 <= timeout <= 2147483.647:
        raise CopilotError("Copilot MCP timeout must be between 0.001 and 2147483.647 seconds")
    names = sorted(name for name in launch.env if not model_environment(name))
    payload = {
        "command": launch.command, "args": launch.args,
        "cwd": launch.cwd, "env": names, "timeout": timeout,
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode("ascii")
    return {
        "type": "local",
        "command": sys.executable,
        "args": ["-m", "qibocal_mcp.copilot_server", encoded],
        "env": {name: f"${{{name}}}" for name in names},
        "tools": list(TOOLS),
        "timeout": timeout * 1000,
    }


def plugin_files(settings: DeploymentSettings) -> dict[str, str]:
    """Render the same plugin for ephemeral launch and persistent export."""
    manifest = {
        "name": NAME, "description": DESCRIPTION, "agents": "agents/",
    }
    profile = {
        "name": NAME,
        "description": DESCRIPTION,
        "tools": [*(f"qibocal/{name}" for name in TOOLS), "ask_user", "view"],
        "mcp-servers": {"qibocal": server_config(launch_for(settings), settings.timeout)},
    }
    return {
        "plugin.json": json.dumps(manifest, indent=2) + "\n",
        f"agents/{NAME}.agent.md": (
            "---\n" + json.dumps(profile, indent=2) + "\n---\n\n"
            + instructions_for() + "\n"
        ),
    }


def write_plugin(directory: Path, files: dict[str, str]) -> None:
    """Write only known renderer-owned paths into a new directory."""
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def export_plugin(directory: Path, settings: DeploymentSettings) -> Path:
    """Refuse existing destinations; never install or launch the plugin."""
    files = plugin_files(settings)
    directory = directory.absolute()
    directory.mkdir(parents=True, exist_ok=False)
    write_plugin(directory, files)
    return directory


def copilot_executable() -> str:
    """Check required public CLI capabilities without a model or hardware call."""
    executable = shutil.which("copilot")
    if executable is None:
        raise CopilotError("Install GitHub Copilot CLI and make copilot available on PATH")
    try:
        result = subprocess.run(
            [executable, "--help"], capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as error:
        raise CopilotError("Could not read copilot --help") from error
    for option in (
        "--plugin-dir", "--agent", "--interactive", "--disable-builtin-mcps",
        "--excluded-tools",
    ):
        if option not in result.stdout:
            raise CopilotError(f"Update GitHub Copilot CLI: required option {option} is missing")
    return executable


def command_for(
    directory: Path,
    executable: str = "copilot",
    objective: str | None = None,
    options: list[str] | None = None,
) -> list[str]:
    """Use identical native profile/tool selection for launch and export."""
    command = [
        executable, "--plugin-dir", str(directory), "--agent", PROFILE_ID,
        "--disable-builtin-mcps", f"--excluded-tools={','.join(EXCLUDED_TOOLS)}",
    ]
    if objective is not None:
        command += ["--interactive", objective]
    return command + (options or [])


def launch_copilot(
    settings: DeploymentSettings,
    objective: str | None = None,
    options: list[str] | None = None,
) -> int:
    """Keep the temporary plugin alive for a foreground interactive session."""
    executable = copilot_executable()
    files = plugin_files(settings)
    with tempfile.TemporaryDirectory(prefix="qibocal-copilot-") as temporary:
        directory = Path(temporary)
        write_plugin(directory, files)
        command = command_for(directory, executable, objective, options)
        process = subprocess.Popen(command)
        previous = signal.getsignal(signal.SIGTERM)
        interrupted_signal = signal.SIGINT

        def terminate(signum, frame):
            nonlocal interrupted_signal
            interrupted_signal = signum
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)
        try:
            returncode = process.wait()
            return returncode if returncode >= 0 else 128 - returncode
        except KeyboardInterrupt:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            return 128 + interrupted_signal
        finally:
            signal.signal(signal.SIGTERM, previous)


def main(argv: list[str] | None = None) -> None:
    """Use shared deployment defaults, leaving models and login to Copilot."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    native = []
    if "--" in arguments:
        split = arguments.index("--")
        native = arguments[split + 1:]
        arguments = arguments[:split]
    parser = argparse.ArgumentParser(
        description=__doc__, argument_default=argparse.SUPPRESS
    )
    parser.add_argument(
        "objective", nargs="?", default=None,
        help="Optional initial bounded objective for an interactive session",
    )
    parser.add_argument(
        "--export-plugin", type=Path, help="Export to a new directory without launching",
    )
    add_agent_deployment_arguments(parser)
    args = parse_settings(parser, "agent", arguments, require_model=False)
    reserved = {
        "--agent", "--plugin-dir", "--interactive", "-i", "--prompt", "-p",
        "--acp", "--available-tools", "--excluded-tools",
    }
    if any(
        option.split("=", 1)[0] in reserved
        or (option.startswith(("-i", "-p")) and not option.startswith("--"))
        for option in native
    ):
        parser.error("Native arguments cannot replace the calibration agent or interactive launch")
    exporting = getattr(args, "export_plugin", None)
    if exporting is not None and (native or args.objective is not None):
        parser.error("--export-plugin cannot be combined with an objective or native arguments")
    try:
        settings = DeploymentSettings.from_namespace(args)
        if exporting is not None:
            directory = export_plugin(exporting, settings)
            print(shlex.join(command_for(directory)))
        else:
            status = launch_copilot(settings, args.objective, native)
            if status:
                raise SystemExit(status)
    except (ConfigError, DeploymentError, CopilotError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
