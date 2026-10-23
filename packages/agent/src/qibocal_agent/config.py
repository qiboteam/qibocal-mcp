"""Persistent agent configurations, kept separate from Qibocamp's files.

The file holds named configurations under ``agents``; ``default`` is the one
used when no name is given. Settings are addressed as ``NAME.setting`` or
``NAME.report.setting``.
"""

import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

type ConfigValue = str | int | float | list[str] | dict[str, ConfigValue]
type Configuration = dict[str, dict[str, ConfigValue]]

DEFAULT = "default"
AGENT_FIELDS = {
    "platform": str,
    "output": str,
    "platforms_dir": str,
    "partition": str,
    "workdir": str,
    "slurm_arg": list,
    "venv": str,
    "platform_repo": list,
    "dependency": list,
    "model": str,
    "model_base_url": str,
    "ssh": str,
    "server_python": str,
    "timeout": float,
}
ENTRY_FIELDS = {**AGENT_FIELDS, "objective": str}
REPORT_FIELDS = {
    "output": str,
    "ssh": str,
    "workdir": str,
    "venv": str,
    "port": int,
    "remote_port": int,
}
REPORT_ENVIRONMENT = "qibocamp"


class ConfigError(ValueError):
    """Invalid or unreadable configuration."""


# Former section names, which would otherwise silently become configurations.
RESERVED_NAMES = ("agent", "agents")


def validate_agent_name(name: str) -> None:
    """Keep names usable as CLI selectors and dotted management keys."""
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is None:
        raise ConfigError("Agent names must contain letters, digits, underscores or hyphens")
    if name in RESERVED_NAMES:
        raise ConfigError(
            f"{name!r} is not a configuration name: the default configuration is "
            "'default', and settings are written as NAME.setting, e.g. default.platform"
        )


def config_directory() -> Path:
    custom = os.environ.get("QIBOCAL_AGENTS_CONFIG_DIR")
    if custom:
        return Path(custom)
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "qibocal"


def get_config_file() -> Path:
    """Locate our file without creating directories or touching sibling files."""
    return config_directory() / "agents.json"


def setting_parts(key: str) -> list[str]:
    """Resolve NAME.setting or NAME.report.setting into a path within the file."""
    parts = key.split(".")
    if len(parts) in (2, 3):
        validate_agent_name(parts[0])
        if len(parts) == 2 and parts[1] in ENTRY_FIELDS:
            return ["agents", *parts]
        if len(parts) == 3 and parts[1] == "report" and parts[2] in REPORT_FIELDS:
            return ["agents", *parts]
    if parts[-1] == "platforms":
        raise ConfigError(f"Unknown setting: {key} (renamed to platforms_dir)")
    raise ConfigError(f"Unknown setting: {key} (use NAME.setting, e.g. default.platform)")


def check_setting(label: str, kind: type, value: object) -> None:
    """Validate stored types without coercing strings, booleans or nulls."""
    if kind is str:
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{label} must be a nonempty string")
    elif kind is list:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ConfigError(f"{label} must be a JSON array of nonempty strings")
    elif kind is int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ConfigError(f"{label} must be an integer from 1 through 65535")
    elif (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value > sys.float_info.max
        or not math.isfinite(value)
    ):
        raise ConfigError(f"{label} must be a positive finite number")
    field = label.rsplit(".", 1)[-1]
    if field == "venv":
        from .environment import DeploymentError, environment_path

        try:
            environment_path(value)
        except DeploymentError as error:
            raise ConfigError(f"{label}: {error}") from error
    elif field == "platform_repo":
        from .environment import DeploymentError, repository_spec

        for item in value:
            try:
                repository_spec(item)
            except DeploymentError as error:
                raise ConfigError(f"{label}: {error}") from error
    elif field == "dependency" and any(item.startswith("-") for item in value):
        raise ConfigError(f"{label} must contain requirements, not installer options")


def field_type(key: str) -> type:
    """Resolve a NAME.setting key and reject unknown names."""
    parts = setting_parts(key)
    return REPORT_FIELDS[parts[-1]] if len(parts) == 4 else ENTRY_FIELDS[parts[-1]]


def validate_value(key: str, value: object) -> None:
    check_setting(key, field_type(key), value)


def validate_config(data: object) -> None:
    """Check the complete file, including configurations not used by this command."""
    if not isinstance(data, dict):
        raise ConfigError("Configuration must be a JSON object")
    for section, values in data.items():
        if section in ("agent", "report"):
            raise ConfigError(
                f"The top-level {section} section was replaced: move its settings "
                f"to agents.default{'.report' if section == 'report' else ''}"
            )
        if section != "agents":
            raise ConfigError(f"Unknown configuration section: {section}")
        if not isinstance(values, dict):
            raise ConfigError("agents must be a JSON object keyed by agent name")
        for name, settings in values.items():
            validate_agent_name(name)
            if not isinstance(settings, dict):
                raise ConfigError(f"agents.{name} must be a JSON object")
            for field, value in settings.items():
                if field == "report":
                    if not isinstance(value, dict):
                        raise ConfigError(f"agents.{name}.report must be a JSON object")
                    for report_field, report_value in value.items():
                        validate_value(f"{name}.report.{report_field}", report_value)
                else:
                    validate_value(f"{name}.{field}", value)


def load_config(
    path: Path | None = None, *, missing_ok: bool = True
) -> Configuration:
    """Read sparse defaults; invalid files never silently become empty defaults."""
    default_location = path is None
    path = path if path is not None else get_config_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        legacy = path.with_name("agent.json")
        if default_location and legacy.is_file():
            print(
                f"Warning: {legacy} is no longer read; the configuration now lives "
                f"in {path}, with the former agent section as agents.default",
                file=sys.stderr,
            )
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


def configured_names(path: Path | None = None, *, enabled: bool = True) -> list[str]:
    """Names stored in the selected file, for selection and listing."""
    if not enabled:
        return []
    return list(load_config(path, missing_ok=path is None).get("agents", {}))


def resolve(
    section: str,
    cli: Mapping[str, object],
    name: str | None = None,
    *,
    config: Path | None = None,
    enabled: bool = True,
    require_model: bool = False,
) -> dict:
    """Merge built-ins, the selected configuration, then explicit CLI values.

    section is "agent" for deployments or "report" for the report server.
    cli holds only the options explicitly given on the command line. Without a
    name, the default configuration is used if stored, or built-ins otherwise.
    """
    data = load_config(config, missing_ok=config is None) if enabled else {}
    agents = data.get("agents", {})
    if name is None:
        name = DEFAULT
    elif name not in agents:
        raise ConfigError(f"Unknown agent: {name}")
    validate_agent_name(name)
    entry = agents.get(name, {})
    stored = {key: value for key, value in entry.items() if key != "report"}
    if section == "agent":
        explicit_interpreter = "server_python" in stored or "server_python" in cli
        defaults = {
            "platform": None, "platforms_dir": None, "partition": None,
            "slurm_arg": [], "platform_repo": [], "dependency": [],
            "model": None, "model_base_url": None, "ssh": None,
            "server_python": None, "timeout": 3600, "objective": None,
            "output": name, "workdir": name,
            # A configuration owns its environment, unless it brings its own.
            "venv": None if explicit_interpreter else name,
        }
        values = defaults | stored | dict(cli)
        fields = ENTRY_FIELDS
    elif section == "report":
        defaults = {
            "output": name, "workdir": name, "ssh": None,
            "venv": REPORT_ENVIRONMENT, "port": 8000, "remote_port": None,
        }
        inherited = {
            key: value for key, value in stored.items()
            if key in ("output", "ssh", "workdir")
        }
        values = defaults | inherited | entry.get("report", {}) | dict(cli)
        if values["remote_port"] is None:
            values["remote_port"] = values["port"]
        fields = REPORT_FIELDS
    else:
        raise ValueError(f"Unknown section: {section}")
    # Relative locations resolve on the execution host, unless given on the CLI.
    values["directories"] = {
        key: values[key]
        for key in ("output", "workdir")
        if key not in cli and not Path(values[key]).is_absolute()
    }
    values["name"] = name
    prefix = f"{name}.report" if section == "report" else name
    for field, kind in fields.items():
        if values.get(field) is not None:
            check_setting(f"{prefix}.{field}", kind, values[field])
    required = ("platform", "model") if require_model else ("platform",)
    if section == "agent":
        for field in required:
            if values[field] is None:
                option = field.replace("_", "-")
                raise ConfigError(f"--{option} is required (or set {name}.{field})")
        if values["slurm_arg"] and values["partition"] is None:
            raise ConfigError("--slurm-arg requires --partition")
        if explicit_interpreter and ("venv" in stored or "venv" in cli):
            raise ConfigError("--venv cannot be combined with --server-python")
    elif values["ssh"] is None and values["remote_port"] != values["port"]:
        raise ConfigError("A distinct --remote-port requires --ssh")
    return values


def edit(path: Path, explicit: bool, action: str, key: str, value: str | None = None):
    """Implement get, set and unset on one NAME.setting key."""
    data = load_config(path, missing_ok=not explicit or action == "set")
    kind = field_type(key)
    parts = setting_parts(key)
    if action == "set":
        parsed = value if kind is str else json.loads(value)
        validate_value(key, parsed)
        container = data
        for part in parts[:-1]:
            container = container.setdefault(part, {})
        container[parts[-1]] = parsed
        save_config(data, path)
        return None
    container = data
    parents = []
    for part in parts[:-1]:
        parents.append((container, part))
        container = container.get(part, {})
    field = parts[-1]
    if field not in container:
        raise ConfigError(f"Setting is not configured: {key}")
    if action == "get":
        stored = container[field]
        return stored if isinstance(stored, str) else json.dumps(stored)
    del container[field]
    for parent, part in reversed(parents):
        if parent.get(part):
            break
        del parent[part]
    save_config(data, path)
    return None
