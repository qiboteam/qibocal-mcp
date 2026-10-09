Command-line reference
======================

Commands below are installed by the project and can be launched with ``uv run``.
Use ``--help`` on a command for its argument parser. Stored defaults, validation
and path semantics are detailed in :doc:`../guides/configuration`.

Common runtime options
----------------------

``qibocal-agent``, ``qibocal-copilot`` and ``qibocal-mcp`` accept:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Option
     - Meaning
   * - ``--config FILE``
     - Read only this JSON configuration file. Missing explicit files error.
   * - ``--no-config``
     - Ignore configuration; mutually exclusive with ``--config``.
   * - ``--platform NAME``
     - Platform name, required at runtime unless provided by the own section.
   * - ``--output PATH``
     - Output directory; defaults to ``runs``.
   * - ``--platforms PATH``
     - Explicit ``QIBOLAB_PLATFORMS`` path list, searched before managed
       repositories; when unset, inherited host paths follow repository roots.
   * - ``--venv NAME_OR_PATH``
     - Managed environment on the execution host. Unset for local/standalone
       execution; SSH agents default to ``qibocal-mcp`` without an explicit
       server interpreter.
   * - ``--platform-repo REPOSITORY``
     - Repeatable trusted repository URL, Git SSH source or deployment-host
       local source, optionally ``#ref=REF&subdirectory=PATH``. Replaces the
       configured list; roots are searched in argument order.
   * - ``--dependency REQUIREMENT``
     - Repeatable additional pip requirement specification; replaces the
       configured list. Resolves together with repository dependency sources.
   * - ``--partition NAME``
     - Dispatch each operation through ``srun``; unset means direct mode.
   * - ``--report-rendering server|client``
     - Default ``server`` renders PNGs on the execution host. ``client`` sends
       Plotly figures to the native Copilot relay, which uses local Chrome.
       Not supported by the Pydantic AI agent.
   * - ``--workdir PATH``
     - Server: SLURM worker directory, default current directory.
       Agent: server launch directory, default unset.
   * - ``--slurm-arg VALUE``
     - Repeatable scheduling options; replaces the configured list and
       requires an effective partition.

Use ``--slurm-arg=--account=ACCOUNT`` for dashed values. Do not redirect
stdio, change task count or detach jobs through extra scheduling options.

``--venv`` accepts a name, absolute path or ``./``/``../`` path and expands
``~`` on the execution host. Named environments use that host's
``$XDG_CACHE_HOME/qibocal/agent/envs`` or ``~/.cache/qibocal/agent/envs``.
Other existing path options remain literal. Repository fragments must have
URL-encoded values as needed; quote arguments containing ``&``.
Cached clones are reused without automatic pull/fetch/reset; pin a commit for
reproducibility. Without a managed environment, repository/dependency options
intentionally modify the selected existing interpreter. Use ``--venv`` for
isolation and trusted sources only. See :doc:`../guides/configuration` for
lookup precedence, dependency discovery and cache/reconciliation semantics.

Shared agent deployment options
-------------------------------

Both ``qibocal-agent`` and ``qibocal-copilot`` additionally accept:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Option
     - Meaning
   * - ``--ssh HOST``
     - SSH host/alias for the access node. Unset launches locally.
   * - ``--server-python EXECUTABLE``
     - Externally managed server interpreter, already containing the server.
       Bypasses automatic environment/core-server installation; cannot coexist
       with ``--venv``. If unset, uses the current interpreter locally unless
       ``--venv`` is supplied, or a managed environment with SSH. Remote paths
       are interpreted remotely.
   * - ``--timeout SECONDS``
     - Positive finite number, default 3600. Per-operation timeout including
       queue wait; not an entire-session budget. Copilot additionally requires
       0.001 through 2147483.647 seconds, inclusive; its adapter converts to
       milliseconds within Node's integer timeout limit.

Both accept a positional agent name, reading ``agents.NAME`` in
``~/.config/qibocal/agent.json`` (or ``$XDG_CONFIG_HOME/qibocal/agent.json``).
Named entries are isolated from unnamed defaults. Without named entries, both
read deployment defaults from the legacy ``agent`` section. They own their MCP
subprocess and supply resolved settings with ``--no-config``.
Neither exposes ``--worker-python``; queued workers use the server interpreter.
SSH managed bootstrap transfers the launching installation's server source and
metadata (from a checkout or installed distribution), not configuration or model
keys; target-host configuration is not read. Managed environments check
dependencies and import the server before launch.

qibocal-agent
-------------

.. code-block:: text

   qibocal-agent [--config FILE | --no-config] [--model MODEL]
                 [--model-base-url URL]
                 [--platform NAME] [--output PATH] [--platforms PATH]
                 [--venv NAME_OR_PATH] [--platform-repo REPOSITORY ...]
                 [--dependency REQUIREMENT ...]
                 [--ssh HOST] [--partition NAME]
                 [--server-python EXECUTABLE] [--workdir PATH]
                 [--slurm-arg VALUE ...] [--timeout SECONDS] [NAME] [OBJECTIVE]

.. list-table:: Pydantic AI options and argument
   :header-rows: 1
   :widths: 30 70

   * - Option/argument
     - Meaning
   * - ``--model MODEL``
     - Required at runtime unless configured in ``agent.model``.
       A vision-capable Pydantic AI model identifier, e.g. ``openai:gpt-4.1``.
       Also accepts ``github-copilot:MODEL_ID`` and ``openwebui:MODEL_ID``.
   * - ``--model-base-url URL``
     - Endpoint override for OpenWebUI or Copilot; can be stored in
       ``agent.model_base_url``. OpenWebUI URLs include ``/api``.
       See :doc:`../guides/model-providers` for credentials and precedence.
   * - ``OBJECTIVE``
     - Calibration goal, hardware limits, execution budget and stopping criteria.
       Overrides ``agents.NAME.objective``; required unless saved on the named
       agent. ``NAME`` alone is sufficient when model and objective are saved.

This remains a one-shot CLI. Model credentials remain in environment/provider
settings; the provider is resolved before deployment or hardware access.
For reusable multi-turn sessions, see :doc:`../agent/index`.

qibocal-copilot
---------------

.. code-block:: text

   qibocal-copilot [--config FILE | --no-config]
                   [--platform NAME] [--output PATH] [--platforms PATH]
                   [--venv NAME_OR_PATH] [--platform-repo REPOSITORY ...]
                   [--dependency REQUIREMENT ...]
                   [--ssh HOST] [--partition NAME]
                   [--server-python EXECUTABLE] [--workdir PATH]
                   [--slurm-arg VALUE ...] [--timeout SECONDS]
                   [--report-rendering server|client]
                   [--export-plugin DIRECTORY | --print-options | --print-options0]
                   [NAME] [OBJECTIVE]
                   [-- COPILOT_OPTIONS ...]

An optional ``OBJECTIVE`` starts an interactive Copilot session (``-i``);
omitting it uses ``agents.NAME.objective`` if saved, otherwise starts a blank
session. ``qibocal-copilot NAME`` still launches by default.
Pass native options after ``--``,
for example ``-- --model MODEL_ID``. Native options cannot replace the selected
profile, plugin directory or interactive execution mode, or override tool
availability through ``--available-tools`` or ``--excluded-tools``.
There are no wrapper ``--model`` or ``--model-base-url`` flags, and
``agent.model``/``agent.model_base_url`` are not required or used.
Copilot owns login, subscription and model selection. Named entries may save
native arguments in ``copilot_options``; explicit arguments after ``--`` follow
them. Both sources must preserve profile, execution mode and tool restrictions,
including ``--disable-builtin-mcps``.

The default launch generates a transient plugin and runs:

.. code-block:: sh

   copilot --plugin-dir PATH \
     --agent qibocal-calibration:qibocal-calibration \
     --disable-builtin-mcps --excluded-tools=skill,sql

The profile picker displays the short name ``qibocal-calibration``.
It neither installs a plugin nor changes global settings or grants blanket
automatic permissions.
Tool restriction requires the profile and both session flags. Selecting an
exported profile in an already-running session without those flags can expose
Copilot's implicit ``skill`` and ``sql`` utilities despite the allowlist.

``--export-plugin DIRECTORY`` writes a persistent plugin to a new directory,
refuses existing targets and prints a launch command. It cannot be combined
with an explicit objective or native options; saved named-agent objectives
and native options are included in the printed command.
Export needs no login, hardware access or
Copilot binary. Use the printed command, which includes the required session
flags. Exported files snapshot deployment/defaults and use runtime
environment ``${NAME}`` references, not secret values. Recorded names exclude
model-provider credential/configuration prefixes and GitHub authentication tokens,
but preserve ``COPILOT_MCP_*`` driver variables. Names must remain defined at
launch; missing names cause a clear relay error. Keep the exporting
installation's interpreter and package available, and re-export after deployment,
policy or forwarded
environment-name set changes.
``--print-options`` instead generates/reuses an immutable plugin under
``$XDG_DATA_HOME/qibocal/agent/plugins`` (default
``~/.local/share/qibocal/agent/plugins``) and prints shell-quoted native
arguments, without a leading executable. It includes the saved/explicit
objective and native options. ``--print-options0`` emits the same argument
vector separated by NUL for lossless shell-array use. Neither requires
Copilot or hardware. Snapshots remain available after the command exits;
changed deployment settings create a new path instead of modifying an active
session's plugin. See :doc:`../agent/copilot` for shell examples.
See :doc:`../agent/copilot` for installation, permissions and resume boundaries.

qibocal-mcp
-----------

.. code-block:: text

   qibocal-mcp [--config FILE | --no-config] [--platform NAME]
               [--output PATH] [--platforms PATH] [--partition NAME]
               [--venv NAME_OR_PATH] [--platform-repo REPOSITORY ...]
               [--dependency REQUIREMENT ...]
               [--worker-python EXECUTABLE] [--workdir PATH]
               [--slurm-arg VALUE ...]

``--worker-python`` selects the SLURM worker interpreter and defaults to the
current Python interpreter. Direct mode does not launch workers.
It cannot be combined with ``--venv``. With ``--venv``, server and queued
workers use the same managed environment.
Without ``--venv``, repository/dependency options with a distinct explicit
``--worker-python`` provision dependencies into both the gateway and worker
interpreters. Both must already contain the server.

The command runs a single-platform stdio server or queued gateway; it takes
no objective, model or SSH option, and has no ``--timeout`` option.
Other clients launch it as their subprocess and configure their own timeouts.
No model API call is made by this command.

qibocal-report-server
---------------------

.. code-block:: text

   qibocal-report-server [--config FILE | --no-config]
                         [NAME]
                         [--output PATH] [--ssh HOST] [--workdir PATH]
                         [--venv NAME_OR_PATH] [--port PORT]
                         [--remote-port PORT]

Start a managed Qibocal Report backend at the agent's report location without
starting calibration or importing hardware. CLI values override the ``report``
section, which overrides ``agent.output``, ``agent.ssh``, ``agent.workdir`` and
an explicitly configured ``agent.venv``. Other agent settings are not inherited.
Without configuration, serve ``runs`` locally using the managed environment
``qibocal-report`` and port ``8000``. The directory is created if absent.
With ``NAME``, the same inheritance uses ``agents.NAME`` and
``agents.NAME.report`` only, so a configured remote agent needs no additional
location or networking flags. Unknown names error.

``--output``, ``--ssh``, ``--workdir`` and ``--venv`` have the same execution-host
path and environment semantics as the agent. ``--port`` chooses the local
registration port. ``--remote-port`` defaults to the effective ``--port``; a
distinct value requires ``--ssh``. Both ports must be integers from 1 to 65535.
There are no platform, model, driver, scheduling or interpreter options.

The command installs a pinned upstream report package in the managed
environment, verifies it, and binds the backend to ``127.0.0.1``.
SSH automatically opens a loopback-only local forward and uses a remote
pseudo-terminal so disconnecting the tunnel terminates the remote server.
After the backend's health endpoint responds, stdout contains the URL to
register in a frontend running on the local machine; provisioning and server
logs are on stderr. Keep the command running while browsing. Ctrl+C stops the
server and forwarding connection.

.. code-block:: sh

   uv run qibocal-report-server --ssh ACCESS_NODE \
     --output /shared/calibration/runs --port 8000 --remote-port 9000

This prints ``http://127.0.0.1:8000``, not the report host's port ``9000``.
For SLURM deployments, the access node must have access to the shared output
directory; the report server does not dispatch through SLURM.
See :doc:`../guides/configuration` for persistent report defaults.

qibocal-mcp-config
------------------

.. code-block:: text

   qibocal-mcp-config [--config FILE] path
   qibocal-mcp-config [--config FILE] show
   qibocal-mcp-config [--config FILE] get KEY
   qibocal-mcp-config [--config FILE] set KEY VALUE
   qibocal-mcp-config [--config FILE] unset KEY

``KEY`` is ``agents.NAME.key`` or ``agents.NAME.report.key`` for named settings,
or legacy ``section.key``, with underscore-separated setting names.
``path`` prints an absolute path without creating anything.
``show`` prints stored JSON only. ``get`` prints strings as plain text and
arrays/numbers as JSON; missing settings error.
``set`` writes sparse validated configuration atomically and creates parent
directories when needed. ``unset`` errors on missing settings and removes
empty sections.

Values for string settings are literal, not JSON-quoted. Use a JSON number for
``timeout``, JSON integers for ``report.port``/``report.remote_port``, and JSON
arrays for ``slurm_arg``, ``platform_repo`` and
``dependency``:

.. code-block:: sh

   uv run qibocal-mcp-config set server.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.model openai:gpt-4.1
   uv run qibocal-mcp-config set agent.venv /shared/qibocal-mcp/env
   uv run qibocal-mcp-config set agent.platform_repo '["https://github.com/YOUR_ORG/platforms.git#ref=COMMIT"]'
   uv run qibocal-mcp-config set agent.dependency '["YOUR_DRIVER_PACKAGE==VERSION"]'
   uv run qibocal-mcp-config set agent.slurm_arg '["--account=quantum", "--time=00:15:00"]'

Set ``agent.partition`` before using that scheduling list at runtime.
``get`` returns these lists as JSON; ``unset`` restores their empty-list defaults
(or the unset default for ``venv``), subject to CLI overrides and the SSH managed
environment default.
The utility has no ``--no-config``, profile or clean command. Full key lists,
file-selection rules and error behavior are in
:doc:`../guides/configuration`.
