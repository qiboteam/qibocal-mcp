"""Persistent CLI defaults, kept separate from Qibocal Report's configuration."""

import argparse
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import TypeAlias

ConfigValue: TypeAlias = str | int | float | list[str] | dict[str, "ConfigValue"]
Configuration: TypeAlias = dict[str, dict[str, ConfigValue]]

COMMON_FIELDS = {
    "platform": str,
    "output": str,
    "platforms_dir": str,
    "partition": str,
    "workdir": str,
    "slurm_arg": list,
    "venv": str,
    "platform_repo": list,
    "dependency": list,
}
FIELDS = {
    "agent": {
        **COMMON_FIELDS,
        "model": str,
        "model_base_url": str,
        "ssh": str,
        "server_python": str,
        "timeout": float,
    },
    "report": {
        "output": str,
        "ssh": str,
        "workdir": str,
        "venv": str,
        "port": int,
        "remote_port": int,
    },
}
NAMED_FIELDS = {**FIELDS["agent"], "objective": str}
# Output folder of the unnamed configuration, alongside named agents' folders.
UNNAMED_OUTPUT = "default"


class ConfigError(ValueError):
    """Invalid or unreadable configuration."""


def validate_agent_name(name: str) -> None:
    """Keep names usable as CLI selectors and dotted management keys."""
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is None:
        raise ConfigError("Agent names must contain letters, digits, underscores or hyphens")


def get_config_file() -> Path:
    """Locate our file without creating directories or touching sibling files."""
    custom = os.environ.get("QIBOCAL_MCP_CONFIG_DIR")
    if custom:
        directory = Path(custom)
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        directory = root / "qibocal"
    return directory / "agent.json"


def setting_parts(key: str) -> list[str]:
    """Resolve a legacy section key or a named agent setting."""
    parts = key.split(".")
    if len(parts) >= 3 and parts[0] == "agents":
        validate_agent_name(parts[1])
        if len(parts) == 3 and parts[2] in NAMED_FIELDS:
            return parts
        if len(parts) == 4 and parts[2] == "report" and parts[3] in FIELDS["report"]:
            return parts
    elif len(parts) == 2 and parts[0] in FIELDS and parts[1] in FIELDS[parts[0]]:
        return parts
    if parts[-1] == "platforms":
        raise ConfigError(f"Unknown setting: {key} (renamed to platforms_dir)")
    raise ConfigError(f"Unknown setting: {key}")


def field_type(key: str) -> type:
    """Resolve a section.key setting and reject unknown names."""
    parts = setting_parts(key)
    if parts[0] == "agents":
        return FIELDS["report"][parts[-1]] if len(parts) == 4 else NAMED_FIELDS[parts[-1]]
    return FIELDS[parts[0]][parts[1]]


def validate_value(key: str, value: object) -> None:
    """Validate stored types without coercing strings, booleans or nulls."""
    kind = field_type(key)
    if kind is str:
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{key} must be a nonempty string")
    elif kind is list:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ConfigError(f"{key} must be a JSON array of nonempty strings")
    elif kind is int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ConfigError(f"{key} must be an integer from 1 through 65535")
    elif (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value > sys.float_info.max
        or not math.isfinite(value)
    ):
        raise ConfigError(f"{key} must be a positive finite number")
    if key.endswith(".venv"):
        from .environment import DeploymentError, environment_path

        try:
            environment_path(value)
        except DeploymentError as error:
            raise ConfigError(f"{key}: {error}") from error
    elif key.endswith(".platform_repo"):
        from .environment import DeploymentError, repository_spec

        for item in value:
            try:
                repository_spec(item)
            except DeploymentError as error:
                raise ConfigError(f"{key}: {error}") from error
    elif key.endswith(".dependency") and any(item.startswith("-") for item in value):
        raise ConfigError(f"{key} must contain requirements, not installer options")


def validate_config(data: object) -> None:
    """Check the complete file, including sections not used by this command."""
    if not isinstance(data, dict):
        raise ConfigError("Configuration must be a JSON object")
    for section, values in data.items():
        if section == "agents":
            if not isinstance(values, dict):
                raise ConfigError("agents must be a JSON object keyed by agent name")
            for name, settings in values.items():
                validate_agent_name(name)
                if not isinstance(settings, dict):
                    raise ConfigError(f"agents.{name} must be a JSON object")
                for field, value in settings.items():
                    key = f"agents.{name}.{field}"
                    if field == "report":
                        if not isinstance(value, dict):
                            raise ConfigError(f"{key} must be a JSON object")
                        for report_field, report_value in value.items():
                            validate_value(f"{key}.{report_field}", report_value)
                    else:
                        validate_value(key, value)
            continue
        if section not in FIELDS:
            raise ConfigError(f"Unknown configuration section: {section}")
        if not isinstance(values, dict):
            raise ConfigError(f"{section} must be a JSON object")
        for name, value in values.items():
            validate_value(f"{section}.{name}", value)


def load_config(
    path: Path | None = None, *, missing_ok: bool = True
) -> Configuration:
    """Read sparse defaults; invalid files never silently become empty defaults."""
    path = path if path is not None else get_config_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        if missing_ok:
            return {}
        raise ConfigError(f"Configuration file not found: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read configuration {path}: {error}") from error
    try:
        validate_config(data)
    except ConfigError as error:
        raise ConfigError(f"Invalid configuration {path}: {error}") from error
    return data


def save_config(data: Configuration, path: Path | None = None) -> None:
    """Replace a validated file atomically, leaving other Qibocal files alone."""
    validate_config(data)
    path = path if path is not None else get_config_file()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    except OSError as error:
        raise ConfigError(f"Could not write configuration {path}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_settings(
    parser: argparse.ArgumentParser, section: str, argv: list[str] | None = None,
    *, require_model: bool = True,
) -> argparse.Namespace:
    """Merge built-ins, the command's file section, then explicit CLI arguments."""
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--config", type=Path, help="Read defaults from this JSON file")
    selection.add_argument(
        "--no-config", action="store_true", help="Ignore configuration files"
    )
    args = parser.parse_intermixed_args(argv)
    path = getattr(args, "config", None)
    try:
        data = (
            {}
            if getattr(args, "no_config", False)
            else load_config(path, missing_ok=path is None)
        )
        selected_name = getattr(args, "agent_name", None)
        target = vars(args).pop("target", None)
        if target is not None:
            if target in data.get("agents", {}) or data.get("agents") or hasattr(args, "objective"):
                selected_name = target
            else:
                args.objective = target
        if selected_name is not None:
            if selected_name not in data.get("agents", {}):
                raise ConfigError(f"Unknown agent: {selected_name}")
            args.agent_name = selected_name
            selected = data["agents"][selected_name]
            agent_defaults = {
                key: value for key, value in selected.items() if key != "report"
            }
            section_defaults = (
                selected.get("report", {}) if section == "report" else agent_defaults
            )
        else:
            agent_defaults = data.get("agent", {})
            section_defaults = data.get(section, {})
        common_defaults = {
            "platform": None,
            "output": UNNAMED_OUTPUT,
            "platforms_dir": None,
            "partition": None,
            "workdir": None,
            "slurm_arg": [],
            "venv": None,
            "platform_repo": [],
            "dependency": [],
        }
        if section == "agent":
            defaults = common_defaults | {
                "model": None, "model_base_url": None, "ssh": None,
                "server_python": None, "timeout": 3600,
                "objective": None,
            }
        else:
            defaults = {
                "output": UNNAMED_OUTPUT, "ssh": None, "workdir": None,
                "venv": None, "port": 8000, "remote_port": None,
            }
        if selected_name is not None:
            defaults |= {
                "output": selected_name, "workdir": selected_name,
                "venv": (
                    None
                    if section == "agent" and (
                        "server_python" in section_defaults or hasattr(args, "server_python")
                    )
                    else selected_name
                ),
            }
        inherited = {}
        if section == "report":
            inherited = {
                name: value
                for name, value in agent_defaults.items()
                if name in {"output", "ssh", "workdir", "venv"}
            }
        values = defaults | inherited | section_defaults | vars(args)
        configured = inherited | section_defaults
        # Reports always land in the execution host's cache unless given on the
        # CLI; a working directory only when configured or implied by a name.
        values["directories"] = {
            name: values[name]
            for name in ("output", "workdir")
            if name not in vars(args) and (
                name == "output" or name in configured or selected_name is not None
            ) and not Path(values[name]).is_absolute()
        }
        if section == "report":
            if values["venv"] is None:
                values["venv"] = "qibocal-report"
            if values["remote_port"] is None:
                values["remote_port"] = values["port"]
        for name in FIELDS[section]:
            if values[name] is not None:
                validate_value(f"{section}.{name}", values[name])
        required = (
            ("platform", "model")
            if section == "agent" and require_model
            else ("platform",)
            if section != "report"
            else ()
        )
        for name in required:
            if values[name] is None:
                key = (
                    f"agents.{selected_name}.{name}"
                    if selected_name is not None else f"{section}.{name}"
                )
                parser.error(f"--{name} is required (or set {key})")
        if section == "report":
            if values["ssh"] is None and values["remote_port"] != values["port"]:
                parser.error("A distinct --remote-port requires --ssh")
        else:
            if values["slurm_arg"] and values["partition"] is None:
                parser.error("--slurm-arg requires --partition")
            if values["venv"] is not None and (
                "server_python" in section_defaults or "server_python" in vars(args)
            ):
                parser.error("--venv cannot be combined with --server-python")
    except ConfigError as error:
        parser.error(str(error))
    return argparse.Namespace(**values)


def add_agent_selection_arguments(parser: argparse.ArgumentParser) -> None:
    """Accept a named agent and an optional objective, retaining legacy objectives."""
    parser.add_argument(
        "target", nargs="?", default=argparse.SUPPRESS,
        help="Configured agent name (or an objective when using unnamed defaults)",
    )
    parser.add_argument(
        "objective", nargs="?", default=argparse.SUPPRESS,
        help="Override the agent's saved bounded calibration objective",
    )


def add_deployment_arguments(parser: argparse.ArgumentParser) -> None:
    """Keep deployment options identical for the server and the agent."""
    parser.add_argument("--venv", help="Managed environment name or path on the execution host")
    parser.add_argument(
        "--platform-repo", action="append",
        help="Git source; optional #ref=REV&subdirectory=PATH (repeatable)",
    )
    parser.add_argument(
        "--dependency", action="append",
        help="Additional Python requirement on the execution host (repeatable)",
    )


def main(argv: list[str] | None = None) -> None:
    """Locate, read and write runtime defaults without importing hardware."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Manage this file instead")
    # Without a subcommand, show where the file is and what it contains.
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("path", help="Print the selected configuration file path")
    commands.add_parser("show", help="Print stored settings as JSON")
    get = commands.add_parser("get", help="Read one section.key setting")
    get.add_argument("key")
    set_value = commands.add_parser("set", help="Write one section.key setting")
    set_value.add_argument("key")
    set_value.add_argument("value", help="Literal string, or JSON for arrays/numbers")
    unset = commands.add_parser("unset", help="Remove one stored setting")
    unset.add_argument("key")
    args = parser.parse_args(argv)
    path = args.config if args.config is not None else get_config_file()
    if args.command == "path":
        print(path.absolute())
        return
    try:
        data = load_config(path, missing_ok=args.config is None or args.command == "set")
        if args.command is None:
            print(f"Configuration file: {path.absolute()}")
            print(json.dumps(data, indent=2))
            return
        if args.command == "show":
            print(json.dumps(data, indent=2))
            return
        kind = field_type(args.key)
        parts = setting_parts(args.key)
        if args.command == "set":
            value = args.value if kind is str else json.loads(args.value)
            validate_value(args.key, value)
            container = data
            for part in parts[:-1]:
                container = container.setdefault(part, {})
            container[parts[-1]] = value
            save_config(data, path)
        else:
            container = data
            parents = []
            for part in parts[:-1]:
                parents.append((container, part))
                container = container.get(part, {})
            name = parts[-1]
            if name not in container:
                raise ConfigError(f"Setting is not configured: {args.key}")
            if args.command == "get":
                value = container[name]
                print(value if isinstance(value, str) else json.dumps(value))
            else:
                del container[name]
                for parent, part in reversed(parents):
                    if parent.get(part):
                        break
                    del parent[part]
                save_config(data, path)
    except (ConfigError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
