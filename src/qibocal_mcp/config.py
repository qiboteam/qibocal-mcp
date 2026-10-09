"""Persistent CLI defaults, kept separate from Qibocal Report's configuration."""

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import TypeAlias

ConfigValue: TypeAlias = str | int | float | list[str]
Configuration: TypeAlias = dict[str, dict[str, ConfigValue]]

COMMON_FIELDS = {
    "platform": str,
    "output": str,
    "platforms": str,
    "partition": str,
    "workdir": str,
    "slurm_arg": list,
}
FIELDS = {
    "server": {**COMMON_FIELDS, "worker_python": str},
    "agent": {
        **COMMON_FIELDS,
        "model": str,
        "model_base_url": str,
        "ssh": str,
        "server_python": str,
        "timeout": float,
    },
}


class ConfigError(ValueError):
    """Invalid or unreadable configuration."""


def get_config_file() -> Path:
    """Locate our file without creating directories or touching sibling files."""
    custom = os.environ.get("QIBOCAL_MCP_CONFIG_DIR")
    if custom:
        directory = Path(custom)
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        directory = root / "qibocal"
    return directory / "qibocal-mcp.json"


def field_type(key: str) -> type:
    """Resolve a section.key setting and reject unknown names."""
    section, separator, name = key.partition(".")
    if not separator or section not in FIELDS or name not in FIELDS[section]:
        raise ConfigError(f"Unknown setting: {key}")
    return FIELDS[section][name]


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
    elif (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value > sys.float_info.max
        or not math.isfinite(value)
    ):
        raise ConfigError(f"{key} must be a positive finite number")


def validate_config(data: object) -> None:
    """Check the complete file, including sections not used by this command."""
    if not isinstance(data, dict):
        raise ConfigError("Configuration must be a JSON object")
    for section, values in data.items():
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
    parser: argparse.ArgumentParser, section: str, argv: list[str] | None = None
) -> argparse.Namespace:
    """Merge built-ins, the command's file section, then explicit CLI arguments."""
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--config", type=Path, help="Read defaults from this JSON file")
    selection.add_argument(
        "--no-config", action="store_true", help="Ignore configuration files"
    )
    args = parser.parse_args(argv)
    path = getattr(args, "config", None)
    try:
        data = (
            {}
            if getattr(args, "no_config", False)
            else load_config(path, missing_ok=path is None)
        )
        defaults = {
            "platform": None,
            "output": "runs",
            "platforms": None,
            "partition": None,
            "workdir": None,
            "slurm_arg": [],
        }
        if section == "server":
            defaults.update(worker_python=sys.executable, workdir=str(Path.cwd()))
        else:
            defaults.update(
                model=None, model_base_url=None, ssh=None, server_python=None,
                timeout=3600,
            )
        values = defaults | data.get(section, {}) | vars(args)
        for name in FIELDS[section]:
            if values[name] is not None:
                validate_value(f"{section}.{name}", values[name])
        for name in ("platform", "model") if section == "agent" else ("platform",):
            if values[name] is None:
                parser.error(f"--{name} is required (or set {section}.{name})")
        if values["slurm_arg"] and values["partition"] is None:
            parser.error("--slurm-arg requires --partition")
    except ConfigError as error:
        parser.error(str(error))
    return argparse.Namespace(**values)


def main(argv: list[str] | None = None) -> None:
    """Locate, read and write server/agent defaults without importing hardware."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Manage this file instead")
    commands = parser.add_subparsers(dest="command", required=True)
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
        if args.command == "show":
            print(json.dumps(data, indent=2))
            return
        kind = field_type(args.key)
        section, name = args.key.split(".")
        if args.command == "set":
            value = args.value if kind is str else json.loads(args.value)
            validate_value(args.key, value)
            data.setdefault(section, {})[name] = value
            save_config(data, path)
        else:
            if name not in data.get(section, {}):
                raise ConfigError(f"Setting is not configured: {args.key}")
            if args.command == "get":
                value = data[section][name]
                print(value if isinstance(value, str) else json.dumps(value))
            else:
                del data[section][name]
                if not data[section]:
                    del data[section]
                save_config(data, path)
    except (ConfigError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
