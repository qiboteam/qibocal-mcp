Persistent configuration
========================

This is the complete settings reference for ``qibocal-agent``. For the few
settings a first run actually needs, see :doc:`../tutorials/interactive`;
everything on this page is optional. The pure ``qibocal-mcp`` server reads no
configuration file, only command-line options, so stored settings on an
execution host can never silently change what a launch does:
``qibocal-agent`` always passes fully resolved settings to the server it
launches, and internal SLURM workers read no user configuration either.

File selection
--------------

Configurations are stored in a JSON file named ``agents.json``. Its location
is selected in this order:

1. If ``QIBOCAL_AGENTS_CONFIG_DIR`` is nonempty:
   ``$QIBOCAL_AGENTS_CONFIG_DIR/agents.json``.
2. Otherwise, if ``XDG_CONFIG_HOME`` is nonempty:
   ``$XDG_CONFIG_HOME/qibocal/agents.json``.
3. Otherwise: ``~/.config/qibocal/agents.json``.

Empty environment values fall through, as in Qibocamp. The folder is
qibocal-agent's own; Qibocamp keeps its files in ``~/.config/qibocamp`` and
writes ``agents.json`` here only for the configurations it manages. ``qibocal-agent config`` prints the selected location
and the file's content.

Two options of ``qibocal-agent``, written before the subcommand, change the
selection:

* ``--config FILE`` reads only that file; a missing explicit file is an error.
* ``--no-config`` ignores configuration files completely.

These options are mutually exclusive. A missing default file means there are
no stored settings. Malformed JSON, unknown keys, unreadable files, invalid
types, empty or whitespace-only strings and invalid timeouts are errors; they
never silently fall back to defaults. The whole file is validated, including
configurations not used by the current command.

The file was previously called ``agent.json``, with a top-level ``agent``
section. That file is no longer read, and a warning says so; move its
``agent`` settings into ``agents.default`` and its ``report`` settings into
``agents.default.report``.

Configurations
--------------

The file holds named configurations under ``agents``. ``default`` is used
whenever no name is given; any other name is selected explicitly. Each entry
takes the settings listed below, plus an optional ``objective`` (nonempty
string) and ``report`` (report server settings):

.. code-block:: json

   {
     "agents": {
       "default": {
         "platform": "YOUR_PLATFORM",
         "model": "openai:gpt-4.1",
         "objective": "Inspect existing evidence only. Do not acquire or update; stop on errors."
       },
       "myremote": {
         "platform": "YOUR_PLATFORM",
         "ssh": "ACCESS_NODE",
         "partition": "PARTITION",
         "workdir": "/shared/calibration",
         "output": "/shared/calibration/runs",
         "report": {"port": 8000}
       }
     }
   }

Every subcommand takes the name as its first argument:

.. code-block:: sh

   qibocal-agent mcp myremote
   qibocal-agent run myremote "YOUR_BOUNDED_OBJECTIVE"
   qibocal-agent report myremote

``qibocal-agent run`` needs a stored or explicit model and a bounded
objective; a single argument is a configuration name if one is stored with it,
and the objective for ``default`` otherwise. ``qibocal-agent mcp`` needs
neither: your interactive client brings its own model.

Names start with a letter or digit and contain only letters, digits,
underscores or hyphens; ``agent`` and ``agents`` are reserved. Unknown names
are errors rather than falling back to another configuration. Configurations
never inherit from each other, ``default`` included. Explicit CLI options
override the selected configuration. With ``--no-config``, the command-line
options alone form an ad-hoc ``default`` configuration.

Settings and built-in defaults
------------------------------

Keys use underscores in JSON; the corresponding CLI options use hyphens.
Every setting is optional in the file. Commands require ``platform``,
supplied by the configuration or the CLI; ``qibocal-agent run`` also requires
``model``. In the table, ``NAME`` is the name of the selected configuration.

.. list-table:: Configuration settings
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
     - Unset; required by ``qibocal-agent run`` only. Pydantic AI model
       identifier, ``github-copilot:MODEL_ID`` or ``openwebui:MODEL_ID``; see
       :doc:`model-providers`.
   * - ``model_base_url``
     - Nonempty string
     - Unset; endpoint override for ``openwebui:`` or ``github-copilot:``.
       OpenWebUI URLs include ``/api``. No credentials in URLs.
   * - ``objective``
     - Nonempty string
     - Unset; the bounded objective of ``qibocal-agent run``.
   * - ``ssh``
     - Nonempty string
     - Unset; SSH host/alias of the access node when provided.
   * - ``partition``
     - Nonempty string
     - Unset; direct execution rather than SLURM dispatch.
   * - ``output``
     - Nonempty string
     - ``NAME``, under the data folder below. Report folder on the execution
       host.
   * - ``workdir``
     - Nonempty string
     - ``NAME``, under the cache folder below. Server launch directory, and
       SLURM worker directory.
   * - ``platforms_dir``
     - Nonempty string
     - Unset; inherits execution-host ``QIBOLAB_PLATFORMS`` after any managed
       repository roots. Explicit path lists take precedence over those roots.
   * - ``venv``
     - Nonempty string
     - ``NAME``, under the cache folder below, unless ``server_python`` is set.
       Managed environment, or an existing one used in place.
   * - ``server_python``
     - Nonempty string
     - Unset. An existing interpreter that already contains the server,
       bypassing the managed environment. Mutually exclusive with ``venv``.
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
     - ``3600`` seconds per operation, including queue waiting. Used by
       ``qibocal-agent run`` and ``qibocal-agent mcp``; interactive clients
       also configure their own.

String values are not coerced from other types. ``null`` and booleans are not
valid settings; a timeout must be numeric, not a quoted string, and cannot
be zero, negative or nonfinite. Use ``unset`` to remove a stored value and
return to the built-in default; do not store ``null``. API keys are not valid
settings: keep credentials in environment or provider settings.

Precedence is **explicit CLI > selected configuration > built-in default**.

Report server settings
----------------------

``qibocal-agent report`` uses **explicit CLI > NAME.report > NAME's locations
> built-in default**. From the configuration itself it only inherits
``output``, ``ssh`` and ``workdir``, so that it serves the reports where the
server writes them. It never uses the calibration environment: the report
server always has its own, ``qibocamp`` unless ``NAME.report.venv`` says
otherwise. A platform or model is not required. Overrides passed to an earlier
command are not persisted: give the same location overrides to ``report``.

The ``report`` object accepts only:

.. list-table::
   :header-rows: 1
   :widths: 22 22 56

   * - Key
     - Type
     - Built-in default and meaning
   * - ``output``
     - Nonempty string
     - The configuration's ``output``. Directory to serve; created if absent.
   * - ``ssh``
     - Nonempty string
     - The configuration's ``ssh``; unset for local reports.
   * - ``workdir``
     - Nonempty string
     - The configuration's ``workdir``. Working directory on the report host.
   * - ``venv``
     - Nonempty string
     - ``qibocamp``. Managed environment on the report host.
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
``agents.json``. Upstream Qibocamp maintains its own server registry.

The latest report ``main`` commit is resolved over HTTPS on each launch,
installed if needed, and its provenance and dependencies checked before launch.
Network or installation failures prevent startup rather than using a stale
version. Report and calibration provisioning share the same environment lock.
No frontend build, Qibolab platform or Chrome setup is required to start the
backend for registration in an existing frontend.

List overrides
--------------

An explicit ``--slurm-arg``, ``--platform-repo`` or ``--dependency`` list
replaces its entire configured list rather than appending to it. Repeated CLI
occurrences form the replacement list:

.. code-block:: sh

   qibocal-agent run --slurm-arg=--account=ACCOUNT \
     --slurm-arg=--time=00:15:00 "YOUR_BOUNDED_OBJECTIVE"

Use the equals form for dashed values. A nonempty effective ``slurm_arg``
requires an effective partition, regardless of whether either came from the
file or CLI. Scheduling options must preserve the worker's stdio and
single-task lifecycle; see :doc:`networking`.

Execution-host paths and environment selection
----------------------------------------------

Relative configured directories are resolved on the execution host,
independently of the JSON file's location or the current working directory:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Setting
     - Base directory
   * - ``venv``
     - ``$XDG_CACHE_HOME/qibocal/agents/envs/``
   * - ``workdir``
     - ``$XDG_CACHE_HOME/qibocal/agents/work/``
   * - ``output``
     - ``$XDG_DATA_HOME/qibocal/agents/runs/``

Reports are user data, so they live under the data home; environments and
working directories can be recreated, so they live in the cache. Both are
scoped to ``agents``, apart from other Qibocal runs. Unset or empty
``XDG_CACHE_HOME`` falls back to ``~/.cache``, and ``XDG_DATA_HOME`` to
``~/.local/share``. Nested relative paths, including ``./`` and ``../``, are
resolved against these bases.

Omitted directories default to the configuration's name. For ``lab``:
``~/.cache/qibocal/agents/envs/lab``, ``~/.cache/qibocal/agents/work/lab``
and ``~/.local/share/qibocal/agents/runs/lab``; for the default one,
``.../default``. The report command uses the same locations unless
overridden. An explicit ``server_python`` suppresses the managed environment.

Absolute paths are not rebased, and ``~`` expands on the execution host,
including remotely. SSH uses the remote host's home and cache environment,
not the client's. Relative managed workdirs are created before launching.
Stored values remain unchanged; configuration management does not create
runtime directories.

Explicit CLI ``--output`` and ``--workdir`` retain their current-directory
semantics and override configured paths. ``~`` in ``platforms_dir`` entries
expands on the execution host, so ``~/platforms`` means the remote home over
SSH. Executable settings remain literal, and environment-variable strings are
not expanded in path values. Prefer absolute shared paths for SSH/SLURM. A
Python executable setting should name an executable, not a shell command such
as ``uv run python``.

``server_python`` and ``venv`` are mutually exclusive; this avoids split
environments. An explicit server interpreter must already contain the server.
Repository and dependency settings still install into that interpreter. This
is an intentional modification; use ``venv`` for isolation. The pure server's
``--worker-python`` option similarly selects an externally managed
compute-node interpreter that must already contain the server.

.. _existing-environments:

Existing environments
---------------------

``venv`` may point to an environment that already exists, for example one
you use for other Qibo work. A valid environment (with ``pyvenv.cfg`` and an
executable ``bin/python``) is then used in place: it is never copied,
recreated or replaced, and an existing folder that is not an environment is
an error. Relative names still resolve under ``qibocal/agents/envs``, so pass
an absolute path, or one starting with ``~``, to select an environment
elsewhere.

When packages have to be installed, conflicts are settled in this order,
highest priority first:

1. the explicit ``dependency`` entries;
2. what the environment already contains;
3. the server's fallback, the pinned Qibocal commit.

In practice, an installed Qibocal is kept with its version and source, and an
editable checkout stays editable. The pinned Qibocal is only installed when
Qibocal is absent. Other installed packages are kept whenever they satisfy
the requirements, and upgraded only when a newly required package needs it,
so every installed package also gets its own dependencies.

Explicit entries can even replace constraints that other packages declare,
for example a driver version older than Qibocal requires. This needs ``uv``
on the execution host, which then applies them as overrides; with ``pip``,
such a conflict is reported as an error instead. A deliberate override may
leave declared requirements unmet: the final consistency check then only
prints a warning.

The server needs Qibocal APIs from
`qiboteam/qibocal#1722 <https://github.com/qiboteam/qibocal/pull/1722>`_,
namely ``Executor.update`` and notes. After any installation, the
provisioning checks that the selected Qibocal provides them, and stops with
the underlying error otherwise. Replacing Qibocal through ``dependency`` thus
works with builds that include those APIs, such as later commits of that pull
request, but not yet with released versions.

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
``qibocal/agents`` cache. Omitting the ref captures the repository's default
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

1. Explicit ``--platforms-dir``/``platforms_dir`` path-list entries, if present.
2. Managed repository roots in their configured order.
3. Inherited execution-host ``QIBOLAB_PLATFORMS``, only when explicit
   ``platforms_dir`` is absent.

With neither repositories nor explicit ``platforms_dir``, the variable is
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

Manage the file without hardware
--------------------------------

The dedicated subcommand is:

.. code-block:: text

   qibocal-agent [--config FILE] config [path|show|get KEY|set KEY VALUE|unset KEY]

``KEY`` is ``NAME.setting``, or ``NAME.report.setting`` for the report server,
for example ``default.platform`` or ``lab.report.port``. Without a
subcommand, it prints the file's location followed by its content. It
operates only on the stored configuration, performs no hardware imports and
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
     - Remove the setting, and the configuration if it becomes empty. A
       missing key is an error.

With explicit ``--config FILE``, missing files are errors for reads and
``unset``, but allowed for ``set``. ``path`` only prints the location.
Writes preserve other valid stored settings; invalid existing files are
reported rather than silently repaired. There are no profile or clean commands.

String settings take literal text, **not JSON-quoted strings**.
``timeout`` takes a JSON numeric value; report ports take JSON integers;
``slurm_arg``, ``platform_repo`` and ``dependency`` take JSON arrays:

.. code-block:: sh

   qibocal-agent config set local.platform YOUR_PLATFORM
   qibocal-agent config set local.model openai:gpt-4.1
   qibocal-agent config set local.objective "YOUR_BOUNDED_OBJECTIVE"
   qibocal-agent config set local.partition PARTITION
   qibocal-agent config set local.venv /shared/qibocal-agent/env
   qibocal-agent config set local.platform_repo '["https://github.com/YOUR_ORG/platforms.git#ref=COMMIT&subdirectory=platforms"]'
   qibocal-agent config set local.dependency '["YOUR_DRIVER_PACKAGE==VERSION"]'
   qibocal-agent config set local.timeout 7200
   qibocal-agent config set local.report.port 8000
   qibocal-agent config set local.slurm_arg '["--account=quantum", "--time=00:15:00"]'
   qibocal-agent config get local.slurm_arg
   qibocal-agent config unset local.slurm_arg

For a separate project-specific file, put ``--config`` before the subcommand:

.. code-block:: sh

   qibocal-agent --config ./agents.json config set local.model openai:gpt-4.1
   qibocal-agent --config ./agents.json config set local.platform YOUR_PLATFORM
   qibocal-agent --config ./agents.json run local "YOUR_BOUNDED_OBJECTIVE"

Do not add comments to the JSON. Omitting settings is how you request built-in
defaults; ``show`` will not fill them in. Storing scheduling arguments before
a partition is set does not make them usable at runtime: the merged runtime
settings must satisfy the partition requirement.
