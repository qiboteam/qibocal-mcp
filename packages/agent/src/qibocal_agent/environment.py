"""Standard-library deployment bootstrap, also transported over SSH."""

import base64
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from importlib import metadata, util
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote


class DeploymentError(RuntimeError):
    """Environment or repository provisioning failed before hardware startup."""


REPORT_REQUIREMENT = (
    "qibocamp @ git+https://github.com/qiboteam/qibocamp.git@main"
)
REPORT_READY_MARKER = "qibocal-mcp:report-listening"


def cache_home() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or "~/.cache").expanduser().resolve()
    return root / "qibocal/agents"


def data_home() -> Path:
    root = Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser().resolve()
    return root / "qibocal/agents"


def directory_path(value: str, folder: str) -> Path:
    """Resolve configured directories on the execution host.

    Reports are user data, kept under the data home; environments and working
    directories can be recreated, so they live in the cache.
    """
    if not value.strip():
        raise DeploymentError("Directory paths must be nonempty")
    path = Path(value).expanduser()
    base = data_home() if folder == "runs" else cache_home()
    return (path if path.is_absolute() else base / folder / path).resolve()


def expand_path_list(value: str) -> str:
    """Expand ``~`` in each entry, using this (execution) host's home."""
    return os.pathsep.join(
        os.path.expanduser(entry) for entry in value.split(os.pathsep)
    )


def environment_path(value: str) -> Path:
    """Keep relative environments separate from Qibocamp's notebook cache."""
    return directory_path(value, "envs")


def prepare_directories(options: dict, arguments: list[str], *, report: bool = False) -> None:
    """Resolve locations before changing cwd or provisioning on the target host."""
    directories = options.get("directories", {})
    output = directories.get("output")
    workdir = directories.get("workdir")
    if output is not None:
        index = 0 if report else arguments.index("--output") + 1
        arguments[index] = str(directory_path(output, "runs"))
    if workdir is not None:
        directory = directory_path(workdir, "work")
        if not Path(workdir).expanduser().is_absolute():
            directory.mkdir(parents=True, exist_ok=True)
        os.chdir(directory)


def repository_spec(value: str) -> tuple[str, str | None, str]:
    """Git sources may select a revision and a relative platform search root."""
    url, separator, fragment = value.partition("#")
    if not url.strip() or url.startswith("-"):
        raise DeploymentError("A platform repository must be a Git URL or path")
    options = {}
    if separator:
        try:
            options = parse_qs(fragment, keep_blank_values=True, strict_parsing=True)
        except ValueError as error:
            raise DeploymentError(f"Invalid repository fragment: {fragment}") from error
        if (
            not options
            or options.keys() - {"ref", "subdirectory"}
            or any(
                len(values) != 1 or not values[0].strip()
                for values in options.values()
            )
        ):
            raise DeploymentError("Repository fragments accept one ref and/or subdirectory")
    ref = options.get("ref", [None])[0]
    if ref is not None and ref.startswith("-"):
        raise DeploymentError("Repository ref must not start with a dash")
    subdirectory = options.get("subdirectory", ["."])[0]
    path = PurePosixPath(subdirectory)
    if path.is_absolute() or ".." in path.parts:
        raise DeploymentError("Repository subdirectory must stay inside the checkout")
    return url, ref, subdirectory


def command(arguments: list[str], *, capture: bool = False) -> str:
    """Installer and Git stdout must never enter the MCP stream."""
    try:
        result = subprocess.run(
            arguments,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else sys.stderr,
            stderr=sys.stderr,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "PIP_NO_INPUT": "1"},
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise DeploymentError(
            f"Deployment command failed: {shlex.join(arguments)}: {error}"
        ) from error
    return result.stdout if capture else ""


@contextlib.contextmanager
def locked(path: Path):
    if os.name != "posix":
        raise DeploymentError("Managed deployment requires a POSIX execution host")
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def fetch_server_source(url: str, commit: str, subdirectory: str) -> Path:
    """Export one project folder of a Git commit into the local cache, once."""
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise DeploymentError(f"Invalid qibocal-agent source commit: {commit}")
    path = PurePosixPath(subdirectory)
    if path.is_absolute() or ".." in path.parts:
        raise DeploymentError(f"Invalid server subdirectory: {subdirectory}")
    destination = cache_home() / "server-sources" / commit
    with locked(destination.with_suffix(".lock")):
        if not destination.exists():
            print(f"Fetching the qibocal-mcp source of {commit[:12]}", file=sys.stderr)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
                clone = Path(temporary) / "clone"
                command([
                    "git", "clone", "--quiet", "--no-checkout",
                    "--filter=blob:none", "--", url, str(clone),
                ])
                archive = Path(temporary) / "source.tar"
                command([
                    "git", "-C", str(clone), "archive", "--format=tar",
                    f"--output={archive}", commit, "--", subdirectory,
                ])
                staged = Path(temporary) / "source"
                with tarfile.open(archive) as stream:
                    stream.extractall(staged, filter="data")
                os.replace(staged, destination)
    root = destination / subdirectory
    if not (root / "pyproject.toml").is_file():
        raise DeploymentError(
            f"No qibocal-mcp project in {subdirectory} at {commit[:12]} of {url}"
        )
    return root


def server_module() -> Path:
    """Locate the qibocal-mcp source matching this installation.

    In order: the installed server, the repository checkout around this file,
    and otherwise the server folder next to where qibocal-agent was installed
    from, i.e. the same Git commit or the same local folder. The orchestration
    then works without installing the server, and its Qibocal stack, locally.
    """
    spec = util.find_spec("qibocal_mcp")
    if spec is not None and spec.origin is not None:
        return Path(spec.origin).resolve().parent
    parents = Path(__file__).resolve().parents
    checkout = parents[3] / "mcp" if len(parents) > 3 else None
    if checkout is not None and (checkout / "pyproject.toml").is_file():
        return checkout / "src" / "qibocal_mcp"
    try:
        origin = json.loads(
            metadata.distribution("qibocal-agent").read_text("direct_url.json") or "{}"
        )
    except metadata.PackageNotFoundError:
        origin = {}
    url = origin.get("url")
    agent = PurePosixPath(origin.get("subdirectory") or ".")
    server = str(agent.parent / "mcp") if agent.name == "agent" else None
    vcs = origin.get("vcs_info", {})
    if url and server and vcs.get("vcs") == "git" and vcs.get("commit_id"):
        return fetch_server_source(url, vcs["commit_id"], server) / "src" / "qibocal_mcp"
    if url and url.startswith("file://") and "dir_info" in origin:
        # A local installation, from the agent folder of a checkout.
        folder = Path(unquote(url.removeprefix("file://"))).parent / "mcp"
        if (folder / "pyproject.toml").is_file():
            return folder / "src" / "qibocal_mcp"
    raise DeploymentError(
        "Managed deployment needs the qibocal-mcp source, which was not found: "
        "install qibocal-agent from the qibocal-mcp Git repository or a "
        "checkout of it, or install qibocal-mcp next to it"
    )


def source_payload() -> str:
    """Ship the qibocal-mcp server source, not local orchestration or settings."""
    module = server_module()
    root = module.parent.parent
    files = {
        f"src/qibocal_mcp/{path.name}": path.read_bytes()
        for path in sorted(module.glob("*.py"))
    }
    if (root / "pyproject.toml").is_file():
        files["pyproject.toml"] = (root / "pyproject.toml").read_bytes()
        files["README.md"] = (root / "README.md").read_bytes()
    else:
        distribution = metadata.distribution("qibocal-mcp")
        dependencies = [
            requirement
            for requirement in distribution.requires or []
            if "extra ==" not in requirement
        ]
        if not any(
            re.match(r"qibocal\s*@\s*git\+", requirement)
            for requirement in dependencies
        ):
            raise DeploymentError(
                "Reinstall qibocal-mcp with its pinned Git dependency "
                "before managed deployment"
            )
        manifest = (
            "[project]\n"
            'name = "qibocal-mcp"\n'
            f"version = {json.dumps(distribution.version)}\n"
            f"requires-python = {json.dumps(distribution.metadata['Requires-Python'])}\n"
            f"dependencies = {json.dumps(dependencies)}\n"
        )
        manifest += "[project.scripts]\n" + "".join(
            f"{json.dumps(entry.name)} = {json.dumps(entry.value)}\n"
            for entry in distribution.entry_points
            if entry.group == "console_scripts"
        )
        manifest += (
            '[build-system]\nrequires = ["uv_build>=0.12,<0.13"]\nbuild-backend = "uv_build"\n'
            '[tool.uv.build-backend]\nmodule-name = "qibocal_mcp"\n'
        )
        files["pyproject.toml"] = manifest.encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, contents in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(contents)
            archive.addfile(entry, io.BytesIO(contents))
    return base64.b64encode(gzip.compress(buffer.getvalue(), mtime=0)).decode("ascii")


def unpack_source(payload: str) -> Path:
    digest = hashlib.sha256(payload.encode()).hexdigest()
    destination = cache_home() / "sources" / digest
    with locked(destination.with_suffix(".lock")):
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
                checkout = Path(temporary) / "source"
                checkout.mkdir()
                with tarfile.open(
                    fileobj=io.BytesIO(base64.b64decode(payload)), mode="r:gz"
                ) as archive:
                    archive.extractall(checkout, filter="data")
                os.replace(checkout, destination)
    return destination


def installer(python: str, operation: str = "install") -> list[str]:
    if shutil.which("uv"):
        return ["uv", "pip", operation, "--python", python]
    has_pip = command(
        [
            python, "-c",
            "import importlib.util; "
            "print(int(importlib.util.find_spec('pip') is not None))",
        ],
        capture=True,
    ).strip()
    if has_pip == "1":
        return [python, "-m", "pip", operation]
    # uv-created and distro environments may be deliberately unseeded.
    if util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "--python", python, operation]
    raise DeploymentError(
        "No package installer is available. Install uv, seed the target environment "
        "with pip (python3-venv/ensurepip), or provide pip >= 22.3 for the bootstrap Python."
    )


def ensure_python(path: Path) -> str:
    python = path / "bin" / "python"
    if not path.exists() and not path.is_symlink():
        print(f"Creating environment: {path}", file=sys.stderr)
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.which("uv"):
            command(["uv", "venv", "--python", sys.executable, str(path)])
        else:
            flags = (
                []
                if util.find_spec("ensurepip") is not None
                else ["--without-pip"]
            )
            command([sys.executable, "-m", "venv", *flags, str(path)])
    if not python.is_file() or not os.access(python, os.X_OK):
        raise DeploymentError(
            f"Environment {path} has no executable bin/python; repair it manually"
        )
    if not (path / "pyvenv.cfg").is_file():
        raise DeploymentError(
            f"{path} is not a virtual environment (pyvenv.cfg is missing); "
            "select an existing interpreter with --server-python instead"
        )
    return str(python)


def checkout_repository(value: str) -> tuple[Path, Path]:
    url, ref, subdirectory = repository_spec(value)
    if Path(url).exists():
        url = str(Path(url).resolve())
    key = hashlib.sha256(json.dumps([url, ref]).encode()).hexdigest()
    checkout = cache_home() / "repositories" / key
    with locked(checkout.with_suffix(".lock")):
        if not checkout.exists():
            checkout.parent.mkdir(parents=True, exist_ok=True)
            print(f"Cloning platform repository: {url}", file=sys.stderr)
            with tempfile.TemporaryDirectory(dir=checkout.parent) as temporary:
                staged = Path(temporary) / "checkout"
                command(["git", "clone", "--", url, str(staged)])
                if ref is not None:
                    command(["git", "-C", str(staged), "fetch", "origin", ref])
                    command(["git", "-C", str(staged), "checkout", "--detach", "FETCH_HEAD"])
                os.replace(staged, checkout)
        if not (checkout / ".git").is_dir():
            raise DeploymentError(f"Invalid cached repository: {checkout}; repair it manually")
        origin = command(
            ["git", "-C", str(checkout), "remote", "get-url", "origin"],
            capture=True,
        ).strip()
        if origin != url:
            raise DeploymentError(f"Cached repository origin does not match {url}")
    platforms = (checkout / subdirectory).resolve()
    if not platforms.is_relative_to(checkout.resolve()) or not platforms.is_dir():
        raise DeploymentError(
            "Platform subdirectory is absent or outside its repository: "
            f"{subdirectory}"
        )
    return checkout, platforms


def repository_dependencies(
    repositories: list[tuple[Path, Path]],
    platform: str,
    search_paths: list[Path] | None = None,
) -> tuple[list[str], list[Path]]:
    packages = []
    requirements = []
    for checkout, platforms in repositories:
        metadata = checkout / "pyproject.toml"
        packaged = (
            (checkout / "setup.py").is_file() or (checkout / "setup.cfg").is_file()
        )
        if metadata.is_file():
            project = tomllib.loads(metadata.read_text(encoding="utf-8"))
            packaged = packaged or "project" in project or "build-system" in project
        if packaged and str(checkout) not in packages:
            packages.append(str(checkout))
        for root in (checkout, platforms):
            path = root / "requirements.txt"
            if path.is_file() and path not in requirements:
                requirements.append(path)
    roots = (
        search_paths if search_paths is not None
        else [root for _, root in repositories]
    )
    for root in roots:
        selected = (root / platform).resolve()
        if not selected.is_relative_to(root.resolve()):
            raise DeploymentError(
                "Selected platform must stay inside its platform search root"
            )
        if selected.exists():
            path = selected / "requirements.txt"
            if path.is_file() and path not in requirements:
                requirements.append(path)
            break
    return packages, requirements


def requirements_fingerprint(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    """Include local -r/-c files so changes to included constraints reconcile too."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen:
        return {}
    seen.add(path)
    contents = path.read_text(encoding="utf-8")
    result = {str(path): hashlib.sha256(os.path.expandvars(contents).encode()).hexdigest()}
    for line in contents.replace("\\\n", "").splitlines():
        tokens = shlex.split(line, comments=True)
        included = None
        if (
            tokens and tokens[0] in {"-r", "--requirement", "-c", "--constraint"}
            and len(tokens) == 2
        ):
            included = tokens[1]
        elif tokens and tokens[0].startswith(("--requirement=", "--constraint=")):
            included = tokens[0].split("=", 1)[1]
        elif tokens and tokens[0].startswith(("-r", "-c")) and len(tokens[0]) > 2:
            included = tokens[0][2:]
        if included is not None and "://" not in included:
            result.update(
                requirements_fingerprint(
                    path.parent / os.path.expandvars(included), seen
                )
            )
    return result


def atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepared_requirements(path: Path, parents: tuple[Path, ...] = ()) -> Path:
    """Rebase local projects per requirements file, without splitting resolution."""
    path = path.resolve()
    if path in parents:
        raise DeploymentError(f"Recursive requirements include: {path}")
    lines = []
    for line in path.read_text(encoding="utf-8").replace("\\\n", "").splitlines():
        include = re.match(
            r"^\s*(-r|-c|--requirement|--constraint)(?:\s+|=)?(\"[^\"]*\"|'[^']*'|\S+)(?:\s+#.*)?$",
            line,
        )
        if include is not None:
            option, value = include.groups()
            value = os.path.expandvars(value.strip("\"'"))
            if "://" not in value:
                included = prepared_requirements(path.parent / value, (*parents, path))
                line = f"{option} {shlex.quote(str(included))}"
        else:
            requirement = re.match(
                r"^(\s*(?:-[ef]\s+|--(?:editable|find-links)(?:=|\s+))?)(\"[^\"]*\"|'[^']*'|\S+)(.*)$",
                line,
            )
            if requirement is not None:
                prefix, value, suffix = requirement.groups()
                if not value.startswith(("'", '"')) and ";" in value:
                    value, marker = value.split(";", 1)
                    suffix = ";" + marker + suffix
                value = os.path.expandvars(value.strip("\"'"))
                project = re.fullmatch(r"(.+?)(\[[^\]]+\])?", value)
                location, extras = project.groups()
                local_file = location.startswith("file:") and not location.startswith("file://")
                links = "-f" in prefix or "--find-links" in prefix
                if local_file:
                    location = location.removeprefix("file:")
                if local_file or location.startswith((".", "/", "~/")) or (
                    "://" not in location
                    and (links or location.endswith((".whl", ".zip", ".tar.gz")))
                ):
                    local = Path(location).expanduser()
                    if not local.is_absolute():
                        local = path.parent / local
                    line = prefix + local.resolve().as_uri() + (extras or "") + suffix
        lines.append(line)
    contents = "\n".join(lines) + "\n"
    key = hashlib.sha256(contents.encode()).hexdigest()
    result = cache_home() / "requirements" / (key + ".txt")
    if not result.is_file():
        atomic_write(result, contents)
    return result


def qibocal_requirement(source: Path) -> tuple[str, str, str]:
    project = tomllib.loads(
        (source / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    dependencies = project["dependencies"]
    for requirement in dependencies:
        match = re.fullmatch(r"qibocal\s*@\s*git\+(.+)@([0-9a-f]{40})", requirement)
        if match is not None:
            url, revision = match.groups()
            return requirement, url, revision
    raise DeploymentError("Server source must declare an immutable Qibocal Git dependency")


def requirement_names(requirements: list[str]) -> set[str]:
    """Extract normalized project names from configured PEP 508 requirements."""
    names = set()
    for requirement in requirements:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        if match is not None:
            names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return names


def installed_distribution_requirement(
    python: str, distribution: str,
) -> list[str] | None:
    """Return installer arguments which preserve an installed distribution itself.

    Requiring the package again makes the installer reconcile its dependencies,
    while retaining its selected version/source.  This is deliberately different
    from freezing the whole environment: dependencies of the retained package may
    still move when its metadata requires it.  Editable installs stay editable,
    so a local checkout keeps being used in place.
    """
    result = command(
        [
            python, "-c",
            "import importlib.metadata as m, json, sys\n"
            "try:\n"
            "    d = m.distribution(sys.argv[1])\n"
            "except m.PackageNotFoundError:\n"
            "    print('null')\n"
            "else:\n"
            "    print(json.dumps({'version': d.version, "
            "'direct_url': json.loads(d.read_text('direct_url.json') or '{}')}))\n",
            distribution,
        ],
        capture=True,
    )
    installed = json.loads(result)
    if installed is None:
        return None
    direct = installed["direct_url"]
    vcs = direct.get("vcs_info", {})
    if direct.get("url") and vcs.get("vcs") and vcs.get("commit_id"):
        return [
            f"{distribution} @ {vcs['vcs']}+{direct['url']}@{vcs['commit_id']}"
        ]
    if direct.get("url") and direct.get("dir_info", {}).get("editable"):
        return ["--editable", direct["url"]]
    if direct.get("url"):
        return [f"{distribution} @ {direct['url']}"]
    return [f"{distribution}=={installed['version']}"]


# Server APIs from qiboteam/qibocal#1722, missing from other Qibocal versions.
QIBOCAL_COMPATIBILITY = (
    "import sys\n"
    "from qibocal import Executor\n"
    "from qibocal.auto.notes import load_notes\n"
    "if not callable(getattr(Executor, 'update', None)):\n"
    "    sys.exit('Executor.update is missing')\n"
)


def check_qibocal(python: str) -> None:
    """Fail before launch when the selected Qibocal cannot serve the tools."""
    try:
        command([python, "-c", QIBOCAL_COMPATIBILITY], capture=True)
    except DeploymentError as error:
        raise DeploymentError(
            "The installed Qibocal cannot provide the APIs required by the "
            "server (Executor.update and notes, from qiboteam/qibocal#1722); "
            "the error above shows why. Check the explicit dependencies, or "
            "remove a qibocal entry to use the pinned version."
        ) from error


def override_file(requirements: list[str]) -> Path:
    """Store explicit requirements for uv, which then win over declared ones."""
    contents = "\n".join(requirements) + "\n"
    key = hashlib.sha256(contents.encode()).hexdigest()
    path = cache_home() / "overrides" / (key + ".txt")
    if not path.is_file():
        atomic_write(path, contents)
    return path


def installed_distribution_source(python: str, distribution: str) -> dict | None:
    """Read direct-install provenance without importing the installed package."""
    result = command(
        [
            python, "-c",
            "import importlib.metadata as m, json, sys\n"
            "try:\n"
            "    d = m.distribution(sys.argv[1])\n"
            "except m.PackageNotFoundError:\n"
            "    print('null')\n"
            "else:\n"
            "    print(d.read_text('direct_url.json') or '{}')\n",
            distribution,
        ],
        capture=True,
    )
    installed = json.loads(result)
    if installed is not None and not isinstance(installed, dict):
        raise DeploymentError(f"Invalid installed {distribution} provenance metadata")
    return installed


def matches_qibocal_pin(installed: dict | None, url: str, revision: str) -> bool:
    vcs = installed.get("vcs_info") if installed is not None else None
    return installed is not None and (
        installed.get("url") == url
        and isinstance(vcs, dict) and vcs.get("commit_id") == revision
    )


def installed_fingerprint(python: str) -> str:
    installed = command(
        [
            python, "-c",
            "import importlib.metadata as m, json; "
            "print(json.dumps(sorted((d.metadata['Name'], d.version, "
            "d.read_text('direct_url.json') or '') "
            "for d in m.distributions())))",
        ],
        capture=True,
    )
    return hashlib.sha256(installed.encode()).hexdigest()


def environment_lock(key: str) -> Path:
    """Serialize all provisioners targeting the same interpreter/environment."""
    return cache_home() / "locks" / (hashlib.sha256(key.encode()).hexdigest() + ".lock")


def provision(options: dict, payload: str | None = None) -> tuple[str, dict[str, str]]:
    """Reconcile all explicitly selected dependencies before starting the server."""
    managed = options.get("venv") is not None
    path = environment_path(options["venv"]) if managed else None
    python = options.get("python") or sys.executable
    lock_key = str(path) if managed else python
    lock_path = environment_lock(lock_key)
    with locked(lock_path):
        if managed:
            python = ensure_python(path)
        version = command(
            [
                python, "-c",
                "import sys; "
                "print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            capture=True,
        ).strip()
        if version not in {"3.12", "3.13"}:
            raise DeploymentError(f"Server Python must be 3.12 or 3.13, found {version}")
        repositories = [
            checkout_repository(value)
            for value in options.get("platform_repo", [])
        ]
        env = dict(os.environ)
        roots = [str(platforms) for _, platforms in repositories]
        explicit = options.get("platforms_dir")
        if explicit is not None:
            explicit = expand_path_list(explicit)
        inherited = env.get("QIBOLAB_PLATFORMS")
        if explicit is not None or roots:
            roots = [*([explicit] if explicit is not None else []), *roots]
            if explicit is None and inherited:
                roots.append(inherited)
            env["QIBOLAB_PLATFORMS"] = os.pathsep.join(roots)
        search_paths = [
            Path(root)
            for root in env.get("QIBOLAB_PLATFORMS", "").split(os.pathsep)
            if root
        ]
        packages, requirements = repository_dependencies(
            repositories, options["platform"], search_paths
        )
        source = unpack_source(payload) if managed and payload is not None else None
        if managed and source is None:
            raise DeploymentError("Managed deployment requires the server source payload")
        pin = qibocal_requirement(source) if source is not None else None
        fingerprint = {
            "source": str(source),
            "python": python,
            "version": version,
            "dependencies": options.get("dependency", []),
            "repositories": [
                [
                    str(checkout),
                    command(
                        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                        capture=True,
                    ).strip(),
                ]
                for checkout, _ in repositories
            ],
            "requirements": {
                name: content
                for requirement in requirements
                for name, content in requirements_fingerprint(requirement).items()
            },
            "metadata": {
                str(checkout / name): (checkout / name).read_text(encoding="utf-8")
                for checkout, _ in repositories
                for name in ("pyproject.toml", "setup.cfg", "setup.py")
                if (checkout / name).is_file()
            },
        }
        digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        state = (
            path / ".qibocal-mcp-state" if managed
            else cache_home() / "states" / lock_path.name
        )
        previous = state.read_text(encoding="utf-8") if state.is_file() else None
        current = digest + ":" + installed_fingerprint(python)
        if previous != current:
            # Priority, highest first: explicit dependencies, what the selected
            # environment already contains, the server's fallback pin.
            explicit = list(options.get("dependency", []))
            arguments = list(explicit)
            needed = source is not None or explicit or packages or requirements
            install = installer(python) if needed else []
            if source is not None:
                # The MCP code goes in without its fallback pin; Qibocal is then
                # required explicitly, as already installed or as the pin.  Keeping
                # it in this resolution also reconciles its dependencies (#17).
                retained_qibocal = installed_distribution_requirement(python, "qibocal")
                command([*install, "--no-deps", str(source)])
                explicit_names = requirement_names(explicit)
                source_dependencies = tomllib.loads(
                    (source / "pyproject.toml").read_text(encoding="utf-8")
                )["project"]["dependencies"]
                arguments += [
                    requirement for requirement in source_dependencies
                    if requirement_names([requirement]).isdisjoint(
                        explicit_names | {"qibocal"}
                    )
                ]
                if "qibocal" not in explicit_names:
                    arguments += retained_qibocal or [pin[0]]
            for package in packages:
                arguments += ["--editable", package]
            for requirement in requirements:
                arguments += ["--requirement", str(prepared_requirements(requirement))]
            if explicit and install[:1] == ["uv"]:
                # Explicit entries also replace constraints declared by other
                # packages; pip cannot do this, and reports a conflict instead.
                arguments += ["--override", str(override_file(explicit))]
            if arguments:
                print(f"Installing deployment dependencies for {python}", file=sys.stderr)
                command([*install, *arguments])
            if source is not None:
                check_qibocal(python)
        command([python, "-c", "import qibocal_mcp.server"], capture=True)
        if managed:
            check = installer(python, "check")
            try:
                command(check)
            except DeploymentError:
                if not (options.get("dependency") and check[:1] == ["uv"]):
                    raise
                # Explicit overrides may deliberately break declared constraints.
                print(
                    "Warning: the environment has unmet requirements, which the "
                    "explicit dependencies may intentionally override",
                    file=sys.stderr,
                )
        state.parent.mkdir(parents=True, exist_ok=True)
        saved_state = digest + ":" + installed_fingerprint(python)
        atomic_write(state, saved_state)
    return python, env


def launch(
    options: dict, server_arguments: list[str], payload: str | None = None
) -> None:
    prepare_directories(options, server_arguments)
    worker_python = options.get("worker_python")
    if worker_python is not None and worker_python != options.get("python"):
        provision({**options, "python": worker_python}, payload)
    python, env = provision(options, payload)
    os.execvpe(python, [python, "-m", "qibocal_mcp.server", *server_arguments], env)


def provision_report(options: dict) -> tuple[str, dict[str, str]]:
    """Reconcile the latest report main commit in a managed environment."""
    path = environment_path(options["venv"])
    with locked(environment_lock(str(path))):
        python = ensure_python(path)
        installed = installed_distribution_source(python, "qibocamp")
        source = REPORT_REQUIREMENT.split(" @ git+", 1)[1]
        expected_url, branch = source.rsplit("@", 1)
        reference = f"refs/heads/{branch}"
        remote = command(
            ["git", "ls-remote", "--exit-code", expected_url, reference],
            capture=True,
        ).split()
        if (
            len(remote) != 2 or remote[1] != reference
            or re.fullmatch(r"[0-9a-f]{40}", remote[0]) is None
        ):
            raise DeploymentError(f"Could not resolve qibocamp branch {branch}")
        expected_revision = remote[0]
        requirement = REPORT_REQUIREMENT.rsplit("@", 1)[0] + "@" + expected_revision
        if not matches_qibocal_pin(installed, expected_url, expected_revision):
            install = installer(python)
            if installed is not None:
                force = "--reinstall" if install[0] == "uv" else "--force-reinstall"
                command([*install, force, "--no-deps", requirement])
            command([*install, requirement])
        installed = installed_distribution_source(python, "qibocamp")
        if not matches_qibocal_pin(installed, expected_url, expected_revision):
            raise DeploymentError("Installed qibocamp does not match the latest main commit")
        command([python, "-c", "import qibocamp.cli"], capture=True)
        command(installer(python, "check"))
    return python, dict(os.environ)


def launch_report(options: dict, report_arguments: list[str]) -> None:
    """Provision and replace the bootstrap with qibocamp."""
    prepare_directories(options, report_arguments, report=True)
    directory = Path(report_arguments[0]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    python, env = provision_report(options)
    expression = "from qibocamp.cli import main; main()"
    if "notify_ready" in options:
        expression = f"""
import socket
import threading
import time

def notify_ready():
    while True:
        try:
            with socket.create_connection(("127.0.0.1", {options["notify_ready"]!r}), timeout=1):
                break
        except (ConnectionError, TimeoutError):
            time.sleep(0.2)
    print("\\n" + {REPORT_READY_MARKER!r}, flush=True)

threading.Thread(target=notify_ready, daemon=True).start()
""" + expression
    os.execvpe(
        python,
        [python, "-c", expression, "report", "server", *report_arguments],
        env,
    )


def main() -> None:
    try:
        request = json.loads(sys.argv[1])
        if request.get("application") == "report":
            launch_report(request["options"], sys.argv[2:])
        else:
            launch(request["options"], sys.argv[2:], request.get("source"))
    except (DeploymentError, OSError, ValueError, tarfile.TarError) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
