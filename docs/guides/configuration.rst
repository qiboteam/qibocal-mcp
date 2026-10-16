Persistent configuration
========================

This is the complete settings reference for the orchestration package.
For the three or four settings a first run actually needs, see
:doc:`../agent/getting-started`; everything on this page is optional.
The pure ``qibocal-mcp`` server reads no configuration file — it takes
command-line flags only — so stored defaults on an execution host can never
silently change what a launch does: ``qibocal-agent`` and
``qibocal-mcp-remote`` always pass fully resolved settings to the server they
launch, and internal SLURM workers read no user configuration either.

File selection
--------------

Configuration is a JSON file named ``agent.json`` with an optional ``agents``
object keyed by agent name, plus optional top-level ``agent`` and ``report``
objects as unnamed defaults. The default path is selected in this order:

1. If ``QIBOCAL_MCP_CONFIG_DIR`` is nonempty:
   ``$QIBOCAL_MCP_CONFIG_DIR/agent.json``.
2. Otherwise, if ``XDG_CONFIG_HOME`` is nonempty:
   ``$XDG_CONFIG_HOME/qibocal/agent.json``.
3. Otherwise: ``~/.config/qibocal/agent.json``.

Empty environment values fall through, as in qibocal-report. This reuses the
configuration folder but configuration management never accesses Qibocal
Report's ``servers.json`` or authentication files.

``qibocal-agent``, ``qibocal-mcp-remote`` and ``qibocal-report-server``
all accept:

* ``--config FILE``: read only that file; an explicitly selected missing file
  is an error.
* ``--no-config``: ignore configuration completely.

These flags are mutually exclusive. A missing default file means there are
no stored defaults. Malformed JSON, unknown keys, unreadable files, invalid
types, empty or whitespace-only strings and invalid timeouts surface errors;
they never silently fall back to defaults. The whole selected file is
validated, including sections not used by the current command.

Named local and remote agents
-----------------------------

Each named entry uses the agent settings listed below, plus optional
``objective`` (nonempty string) and ``report`` (report settings object):

.. code-block:: json

   {
     "agents": {
       "local": {
         "platform": "YOUR_PLATFORM",
         "model": "openai:gpt-4.1",
         "objective": "Inspect existing evidence only. Do not acquire or update; stop on errors.",
         "output": "/absolute/local/runs"
       },
       "myremote": {
         "platform": "YOUR_PLATFORM",
         "ssh": "ACCESS_NODE",
         "partition": "PARTITION",
         "workdir": "/shared/calibration",
         "output": "/shared/calibration/runs",
         "report": {"venv": "qibocal-report", "port": 8000}
       }
     }
   }

Select an entry with its name:

.. code-block:: sh

   qibocal-agent local
   qibocal-mcp-remote myremote
   qibocal-report-server myremote

``qibocal-agent NAME`` needs a saved model and bounded objective; pass a
second positional argument to override the objective. ``qibocal-mcp-remote``
needs neither: your interactive client brings its own model.
Names start with a letter or digit and contain only letters, digits,
underscores or hyphens. Unknown names error rather than falling back to another
deployment. Named entries do not inherit top-level unnamed defaults or each
other's settings. Explicit CLI flags still override the selected entry.
With no named entries, the old positional-objective syntax remains supported;
use ``--no-config`` for an ad-hoc launch alongside named configurations.

Settings and built-in defaults
------------------------------

Keys use underscores in JSON; the corresponding CLI flags use hyphens.
Every setting is optional in the file. Calibration commands require
``platform`` supplied by configuration or CLI at runtime; ``qibocal-agent``
also requires ``model``.

.. list-table:: Agent settings (unnamed ``agent`` section and named entries)
   :header-rows: 1
   :widths: 22 20 58

   * - Key
     - Type
     - Built-in default and meaning
   * - ``platform``
     - Nonempty string
     - Unset; required at runtime. Installed platform name.
   * - ``model``
     - Nonempty string
     - Unset; required by ``qibocal-agent`` only. Pydantic AI model identifier,
       ``github-copilot:MODEL_ID`` or ``openwebui:MODEL_ID``; see
       :doc:`model-providers`.
   * - ``model_base_url``
     - Nonempty string
     - Unset; endpoint override for ``openwebui:`` or ``github-copilot:``.
       OpenWebUI URLs include ``/api``. No credentials in URLs.
   * - ``ssh``
     - Nonempty string
     - Unset; SSH host/alias of the access node when provided.
   * - ``partition``
     - Nonempty string
     - Unset; direct execution rather than SLURM dispatch.
   * - ``output``
     - Nonempty string
     - Agent name for named entries; ``runs`` for unnamed invocations.
       Artifact output directory on the execution host.
   * - ``workdir``
     - Nonempty string
     - Agent name for named entries; otherwise no working-directory override.
       Server launch directory (and SLURM worker directory).
   * - ``platforms``
     - Nonempty string
     - Unset; inherits execution-host ``QIBOLAB_PLATFORMS`` after any managed
       repository roots. Explicit path lists take precedence over those roots.
   * - ``venv``
     - Nonempty string
     - Agent name for named entries unless ``server_python`` is explicit.
       Unnamed invocations leave it unset; unnamed SSH launches without
       explicit ``server_python`` default to ``qibocal-mcp``.
   * - ``server_python``
     - Nonempty string
     - Unset; current interpreter locally, managed environment with SSH.
       Explicit values select a preinstalled server and bypass automatic
       environment/core-server installation. Mutually exclusive with ``venv``.
   * - ``platform_repo``
     - JSON array of nonempty strings
     - ``[]``. Trusted platform repositories, in lookup order.
   * - ``dependency``
     - JSON array of nonempty strings
     - ``[]``. Additional Python requirement specifications.
   * - ``slurm_arg``
     - JSON array of nonempty strings
     - ``[]``. Additional ``srun`` options; a nonempty list requires an
       effective ``partition``.
   * - ``timeout``
     - Positive finite JSON number
     - ``3600`` seconds per operation, including queue waiting.
       Used by ``qibocal-agent``; interactive clients configure their own.

String values are not coerced from other types. ``null`` and booleans are not
valid settings; a timeout must be numeric, not a quoted string, and cannot
be zero, negative or nonfinite.
Use ``unset`` to remove a stored value and return to the built-in default
(unless explicitly overridden on the CLI); do not store ``null``.

Objectives can be saved only on named agents, under ``agents.NAME.objective``.
Supply operator-approved limits, an execution budget and stopping criteria.
API keys are not valid configuration settings; keep credentials in
environment/provider settings.

Precedence is **explicit CLI > command's own section > built-in default**.
Selecting a name reads ``agents.NAME`` only.

Report server defaults
----------------------

``qibocal-report-server`` uses **explicit CLI > report section > selected agent
settings > built-in default**. It inherits only ``agent.output``, ``agent.ssh``,
``agent.workdir`` and an explicitly configured ``agent.venv`` for unnamed
defaults. With a name, it inherits those fields from ``agents.NAME`` and uses
``agents.NAME.report`` instead of the top-level ``report`` section. A platform or
model is not required, and scheduling, driver dependencies and interpreter
settings are not inherited. Overrides passed to an earlier agent invocation
are not persisted: supply the same location overrides to the report command.

The ``report`` section accepts only:

.. list-table::
   :header-rows: 1
   :widths: 22 22 56

   * - Key
     - Type
     - Built-in default and meaning
   * - ``output``
     - Nonempty string
     - Agent name for named entries; ``runs`` otherwise.
       Directory to serve; created if absent.
   * - ``ssh``
     - Nonempty string
     - Unset; local execution. Otherwise SSH host/alias containing the reports.
   * - ``workdir``
     - Nonempty string
     - Agent name for named entries; unset otherwise.
       Working directory on the report host, as for the agent.
   * - ``venv``
     - Nonempty string
     - Agent name for named entries; ``qibocal-report`` otherwise.
       Always-managed environment on the report host.
       Explicit agent environments are inherited.
   * - ``port``
     - JSON integer, 1 through 65535
     - ``8000``. Local port used in the frontend registration URL.
   * - ``remote_port``
     - JSON integer, 1 through 65535
     - Effective ``port``. Report-host listening port; a distinct value requires SSH.

The report server binds to loopback only. SSH automatically forwards
``127.0.0.1:port`` locally to ``127.0.0.1:remote_port`` on the report host.
Health checks through the tunnel begin only after the remote server signals
that it is listening, avoiding SSH connection-refused messages during startup.
The URL is printed after the health endpoint responds; installation and server
logs go to stderr. The server and tunnel remain attached until Ctrl+C.
With SLURM, serve the shared output directory on the access node, not a
compute-node job. The bootstrap uses resolved settings, not the remote host's
``agent.json``. Upstream Qibocal Report maintains its own server registry.

The latest report ``main`` commit is resolved over HTTPS on each launch,
installed if needed, and its provenance and dependencies checked before launch.
Network or installation failures prevent startup rather than using a stale
version. A dedicated report environment is recommended to avoid
changing an agent environment's dependencies; use ``report.venv`` to override
an inherited environment. Report and calibration provisioning share the same
environment lock. No frontend build, Qibolab platform or Chrome setup is
required to start the backend for registration in an existing frontend.

Agent list overrides and isolation
----------------------------------

An explicit ``--slurm-arg``, ``--platform-repo`` or ``--dependency`` list
replaces its entire configured list rather than appending to it. Repeated CLI
occurrences form the replacement list:

.. code-block:: sh

   qibocal-agent --slurm-arg=--account=ACCOUNT \
     --slurm-arg=--time=00:15:00 "YOUR_BOUNDED_OBJECTIVE"

Use the equals form for dashed values. A nonempty effective ``slurm_arg``
requires an effective partition, regardless of whether either came from the
file or CLI. Scheduling options must preserve the worker's stdio and
single-task lifecycle; see :doc:`networking`.

Execution-host paths and environment selection
----------------------------------------------

Relative configured directories are resolved under the execution host's cache
root, independently of the JSON file's location or current working directory:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Setting
     - Base directory
   * - ``venv``
     - ``$XDG_CACHE_HOME/qibocal/agent/envs/``
   * - ``workdir``
     - ``$XDG_CACHE_HOME/qibocal/agent/work/``
   * - ``output``
     - ``$XDG_CACHE_HOME/qibocal/agent/runs/``

Unset or empty ``XDG_CACHE_HOME`` falls back to ``~/.cache``. Nested relative
paths, including ``./`` and ``../``, are resolved against these bases.
For a named agent ``lab``, omitted directories default to ``lab``:
``~/.cache/qibocal/agent/envs/lab``, ``~/.cache/qibocal/agent/work/lab``
and ``~/.cache/qibocal/agent/runs/lab``. The report command uses the same
locations unless overridden. An explicit ``server_python`` suppresses the
implicit managed agent environment.

Absolute paths are not rebased, and ``~`` expands on the execution host,
including remotely. SSH uses the remote host's home and cache environment,
not the client's. Relative managed workdirs are created before launching.
Stored values remain unchanged; configuration management does not create
runtime directories.

Explicit CLI ``--output`` and ``--workdir`` retain their current-directory
semantics and override configured paths. Unnamed invocations retain their
existing omitted-directory defaults. ``platforms`` and executable settings
remain literal; environment-variable strings are not expanded in path values.
Prefer absolute shared paths for SSH/SLURM. A Python executable setting should
name an executable, not a shell command such as ``uv run python``.

Unnamed SSH launches without explicit ``server_python`` provision the named
``qibocal-mcp`` environment even when ``venv`` is unset. Named agents default
to their own managed environment locally and remotely. Unnamed local launches
manage environments only with an effective ``venv``.
``server_python`` and ``venv`` are mutually exclusive; this avoids split
environments. An explicit server interpreter must already contain the server.
Repository and dependency settings still install into that interpreter, or the
selected existing interpreter when no managed environment is requested. This is
an intentional modification; use ``venv`` for isolation.
The pure server's ``--worker-python`` flag similarly selects an externally
managed compute-node interpreter that must already contain the server.

Platform repositories and dependency sources
--------------------------------------------

Each ``platform_repo`` entry is a repository URL, Git SSH source or local
source path; local source paths are interpreted on the deployment host, not
the laptop when using SSH. An optional fragment selects a ref and platform
root:

.. code-block:: sh

   --platform-repo 'https://github.com/YOUR_ORG/platforms.git#ref=COMMIT&subdirectory=platforms'
   --platform-repo 'git@YOUR_GIT_HOST:YOUR_ORG/platforms.git#ref=release%2Fv1'
   --platform-repo /shared/repositories/site-platforms

Replace these illustrative sources with reviewed repositories. URL-encode ref
and subdirectory values as needed, and quote shell arguments containing ``&``.
``ref`` may name a branch, tag or commit.
Without ``subdirectory``, platform folders are looked up at the checkout root.
A subdirectory must be relative and stay inside the checkout: traversal,
absolute paths and symlink escapes are rejected.

Each URL/ref combination has its own cached clone under the execution host's
``qibocal/agent`` cache. Omitting the ref captures the repository's default
branch on the first clone. Subsequent launches reuse clones **without automatic
pull, fetch or reset**, preventing unexpected hardware-definition changes.
Pin a commit for reproducibility. To refresh, choose a new ref or explicitly
review and maintain the cache; changing a branch upstream alone does not
refresh an existing clone. Array/CLI order determines repository lookup order.
During calibration, the agent can explicitly select newly published paired
parameters/calibration files with
``start_session(source="repository", fetch=true, ref="main")``. This fetches
from the checkout's configured ``origin`` (or another existing ``remote``)
without changing checkout refs/files, reinstalling dependencies or reloading
drivers. See :doc:`../reference/mcp` for remote revision selection and
provenance.

The effective ``QIBOLAB_PLATFORMS`` search path is:

1. Explicit ``--platforms``/``platforms`` path-list entries, if present.
2. Managed repository roots in their configured order.
3. Inherited execution-host ``QIBOLAB_PLATFORMS``, only when explicit
   ``platforms`` is absent.

With neither repositories nor explicit ``platforms``, the variable is
untouched. An explicit path list replaces the inherited value, not the managed
roots. Across SSH, the inherited value is remote, not the laptop's environment.

A repository with root packaging metadata (``pyproject.toml`` containing
``[project]`` or ``[build-system]``, ``setup.py`` or ``setup.cfg``) is installed
editable for its dependencies and imports. ``requirements.txt`` at each
checkout root and configured subdirectory root is honored. Per-platform
requirements are read only from the first matching platform across the
effective lookup paths, including explicit or inherited paths when provisioning
is active. Duplicate files are removed; unrelated platforms are not scanned.
Cached copies rebase local projects, editable references, wheels,
``--find-links`` and local ``-r``/``-c`` includes relative to their original
requirements file, not the server working directory. Recursive include cycles
are errors. Remote includes are handled by the installer; their changing
contents are not locally fingerprinted.

``dependency`` entries are pip requirement specifications, installed together
with packaged repositories and discovered requirements. None of these sources
overrides another: the installer resolves them together, and conflicts are
errors. Managed environments also honor the core Qibocal pin from the shipped
``qibocal-mcp`` source (or installed distribution metadata when no checkout is
available). All package metadata, dependency and requirements sources are
installed in one resolver request. Installation is additive, not exact
synchronization; removing a configured dependency does not uninstall it.
Successful dependency state is fingerprinted, including nested local
``-r``/``-c`` files, root packaging metadata and repository Git ``HEAD``.
Environment-variable substitutions and installed package names, versions and
direct-source metadata are also fingerprinted, so manual uninstall/upgrades
trigger reconciliation. Managed Qibocal's installed Git commit is verified
explicitly; a different source reporting the same package version is
selectively reinstalled before combined dependency resolution and checked again
before recording success. Managed environments run an installer dependency
check and import the server before launch; failed installation/checks do not
silently continue. Existing broken or custom environments are never
automatically deleted or recreated.

Managed deployment is POSIX-only and uses Python 3.12 or 3.13. Existing
``--venv`` paths must contain ``pyvenv.cfg`` and executable ``bin/python``;
use ``--server-python`` for a conda/system interpreter instead.
Provisioning locks serialize installation, not the server's entire lifetime.
Use separate environment names or paths for concurrent deployments with
different source/dependency settings.

Only configure trusted sources: package metadata and requirements can execute
code. Remote Git authentication must work independently; the laptop's
``SSH_AUTH_SOCK`` is not automatically forwarded for remote cloning.
Never store credentials in repository URLs or configuration. See
:doc:`networking` for host environments and shared caches, and
:doc:`../agent/installation` for remote bootstrap prerequisites.

Manage defaults without hardware
--------------------------------

The dedicated command is:

.. code-block:: text

   qibocal-mcp-config [--config FILE] path|show|get KEY|set KEY VALUE|unset KEY

``KEY`` is ``agents.NAME.key`` or ``agents.NAME.report.key`` for named agents,
or ``section.key`` for the unnamed ``agent`` and ``report`` defaults.
It operates only on stored configuration, performs no hardware imports and
does not connect to instruments.

.. list-table:: Subcommands
   :header-rows: 1
   :widths: 20 80

   * - Subcommand
     - Behavior
   * - ``path``
     - Print the absolute selected file path without creating anything.
   * - ``show``
     - Print stored JSON only, not built-in or effective defaults. A missing
       default file displays an empty object.
   * - ``get KEY``
     - Return a string as plain text, or an array/number as JSON. Error if
       the setting is missing.
   * - ``set KEY VALUE``
     - Validate and write a sparse configuration atomically, creating parent
       directories as needed. A missing file is allowed.
   * - ``unset KEY``
     - Remove the setting and remove its section if empty. A missing key
       is an error.

With explicit ``--config FILE``, missing files are errors for reads and
``unset``, but allowed for ``set``. ``path`` only prints the location.
Writes preserve other valid stored settings; invalid existing files are
reported rather than silently repaired. There are no profile or clean commands.

String settings take literal text, **not JSON-quoted strings**.
``timeout`` takes a JSON numeric value; report ports take JSON integers;
``slurm_arg``, ``platform_repo`` and ``dependency`` take JSON arrays:

.. code-block:: sh

   qibocal-mcp-config set agents.local.platform YOUR_PLATFORM
   qibocal-mcp-config set agents.local.model openai:gpt-4.1
   qibocal-mcp-config set agents.local.objective "YOUR_BOUNDED_OBJECTIVE"
   qibocal-mcp-config set agents.local.partition PARTITION
   qibocal-mcp-config set agents.local.venv /shared/qibocal-agent/env
   qibocal-mcp-config set agents.local.platform_repo '["https://github.com/YOUR_ORG/platforms.git#ref=COMMIT&subdirectory=platforms"]'
   qibocal-mcp-config set agents.local.dependency '["YOUR_DRIVER_PACKAGE==VERSION"]'
   qibocal-mcp-config set agents.local.timeout 7200
   qibocal-mcp-config set agents.local.report.port 8000
   qibocal-mcp-config set agents.local.slurm_arg '["--account=quantum", "--time=00:15:00"]'
   qibocal-mcp-config get agents.local.slurm_arg
   qibocal-mcp-config unset agents.local.slurm_arg

For a separate project-specific file, put ``--config`` before the subcommand:

.. code-block:: sh

   qibocal-mcp-config --config ./agent.json set agents.local.model openai:gpt-4.1
   qibocal-mcp-config --config ./agent.json set agents.local.platform YOUR_PLATFORM
   qibocal-agent --config ./agent.json local "YOUR_BOUNDED_OBJECTIVE"

Do not add comments to the JSON. Omitting settings is how you request built-in
defaults; ``show`` will not fill them in. Storing scheduling arguments before
a partition is set does not make them usable at runtime: the merged runtime
settings must satisfy the partition requirement.
