Command-line reference
======================

``qibocal-mcp`` is installed by the ``qibocal-mcp`` package; the other
commands by ``qibocal-agent``. Use ``--help`` on a command for its argument
parser. Stored defaults, validation and path semantics are detailed in
:doc:`../guides/configuration`.

qibocal-mcp
-----------

.. code-block:: text

   qibocal-mcp --platform NAME [--output PATH] [--platforms-dir PATH]
               [--partition NAME] [--worker-python EXECUTABLE]
               [--workdir PATH] [--slurm-arg VALUE ...]

The MCP server itself: a single-platform stdio server (no ``--partition``) or
a queued access-node gateway. It reads **no configuration file** and has no
SSH, model or timeout options; clients launch it as their subprocess and
configure their own timeouts. No model API call is made by this command.

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Option
     - Meaning
   * - ``--platform NAME``
     - Required platform name.
   * - ``--output PATH``
     - Report output directory, default ``runs``.
   * - ``--platforms-dir PATH``
     - Set ``QIBOLAB_PLATFORMS`` for this process; ``~`` in each entry
       expands to this host's home.
   * - ``--partition NAME``
     - Dispatch each operation through ``srun``; unset means direct mode.
   * - ``--worker-python EXECUTABLE``
     - SLURM worker interpreter, default the current interpreter. It must
       already contain the server. Direct mode launches no workers.
   * - ``--workdir PATH``
     - SLURM worker working directory, default the current directory.
   * - ``--slurm-arg VALUE``
     - Repeatable extra ``srun`` options; requires ``--partition``. Use
       ``--slurm-arg=--account=ACCOUNT`` for dashed values. Do not redirect
       stdio, change task count or detach jobs.

Common orchestration options
----------------------------

``qibocal-agent`` and ``qibocal-mcp-remote`` resolve the same deployment
settings and accept:

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
     - Platform name, required at runtime unless configured.
   * - ``--output PATH``
     - Output directory on the execution host; defaults to the agent name for
       named agents, ``default`` otherwise, under the execution host's
       ``~/.cache/qibocal/agent/runs/``.
   * - ``--ssh HOST``
     - SSH host/alias for the access node. Unset launches locally.
   * - ``--partition NAME``
     - Dispatch each operation through ``srun`` on the execution host.
   * - ``--platforms-dir PATH``
     - Explicit ``QIBOLAB_PLATFORMS`` path list on the execution host, searched
       before managed repositories; when unset, inherited host paths follow
       repository roots. ``~`` expands on the execution host, not locally.
   * - ``--venv NAME_OR_PATH``
     - Managed environment on the execution host. Named agents default to
       their name unless a server interpreter is explicit. Unnamed local
       launches leave it unset; unnamed SSH launches default to
       ``qibocal-mcp`` without an explicit server interpreter.
   * - ``--server-python EXECUTABLE``
     - Externally managed server interpreter, already containing the server.
       Bypasses automatic environment/core-server installation; cannot coexist
       with ``--venv``. Remote paths are interpreted remotely.
   * - ``--platform-repo REPOSITORY``
     - Repeatable trusted repository URL, Git SSH source or deployment-host
       local source, optionally ``#ref=REF&subdirectory=PATH``. Replaces the
       configured list; roots are searched in argument order.
   * - ``--dependency REQUIREMENT``
     - Repeatable additional pip requirement specification; replaces the
       configured list. Resolves together with repository dependency sources.
   * - ``--workdir PATH``
     - Server launch directory on the execution host (and SLURM worker
       directory); defaults to the agent name for named agents.
   * - ``--slurm-arg VALUE``
     - Repeatable scheduling options; replaces the configured list and
       requires an effective partition.
   * - ``--timeout SECONDS``
     - Positive finite number, default 3600. Per-operation timeout including
       queue wait; used by the agent's own MCP client. Interactive clients
       behind ``qibocal-mcp-remote`` configure their own timeout instead.

Both accept a positional agent name, reading ``agents.NAME`` in
``~/.config/qibocal/agent.json`` (or ``$XDG_CONFIG_HOME/qibocal/agent.json``).
Named entries are isolated from unnamed defaults. Queued workers use the
server interpreter; neither command exposes ``--worker-python``.
SSH managed bootstrap transfers the ``qibocal-mcp`` source and metadata (from
a checkout or installed distribution), not configuration or model keys;
target-host configuration is not read. Managed environments check dependencies
and import the server before launch. If the selected environment already has
Qibocal, its installed version and source are retained and its declared
dependencies are reconciled. Explicit ``--dependency`` requirements take
precedence, while the server's pinned Qibocal is used only when Qibocal is
absent. The transferred MCP source is installed without applying that fallback
pin to an existing environment.

qibocal-agent
-------------

.. code-block:: text

   qibocal-agent [--config FILE | --no-config] [--model MODEL]
                 [--model-base-url URL] [COMMON OPTIONS ...]
                 [NAME] [OBJECTIVE]

.. list-table:: Pydantic AI options and arguments
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
     - Calibration goal, hardware limits, execution budget and stopping
       criteria. Overrides ``agents.NAME.objective``; required unless saved on
       the named agent. ``NAME`` alone is sufficient when model and objective
       are saved.

This is a one-shot CLI. The model provider is resolved before deployment or
hardware access; credentials remain in environment/provider settings. The
calibration policy itself is fetched from the server's ``calibrate`` prompt.
For reusable multi-turn sessions, see :doc:`../agent/index`.

qibocal-mcp-remote
------------------

.. code-block:: text

   qibocal-mcp-remote [--config FILE | --no-config] [COMMON OPTIONS ...] [NAME]

Serve the calibration MCP over stdio for an interactive client. The command
resolves deployment settings exactly like the agent (minus model settings) and
launches the server: the local server command, or SSH carrying the same stdio
to the access node, with managed provisioning when required. It then stays in
between as a thin proxy, forwarding the server's tools, prompt and resource
and adding two of its own:

* ``list_configurations`` — the stored configurations (the unnamed default as
  ``null`` and every ``agents.NAME``), with their platform, SSH host,
  partition and output, and which one is active.
* ``switch_configuration(name)`` — close the active server and launch the
  selected configuration (``null`` for the unnamed default). Run handles of
  the closed server expire.

The configuration file is read again on every switch, so entries added during
a session are usable without restarting. CLI options apply only to the
configuration selected at startup; with ``--no-config``, switching is disabled.
If a launch fails, the proxy stays up with only the two switching tools, so
another configuration can be chosen. Register it in your MCP client
configuration; see :doc:`../tutorials/interactive`. It requires a platform but no
model, makes no model API calls and prints nothing on stdout besides the MCP
stream.

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
an explicitly configured ``agent.venv``. Other agent settings are not
inherited. Without configuration, serve ``~/.cache/qibocal/agent/runs/default``
locally using the managed
environment ``qibocal-report`` and port ``8000``. The directory is created if
absent. With ``NAME``, the same inheritance uses ``agents.NAME`` and
``agents.NAME.report`` only, so a configured remote agent needs no additional
location or networking flags. Unknown names error.

``--output``, ``--ssh``, ``--workdir`` and ``--venv`` have the same
execution-host path and environment semantics as the agent. ``--port`` chooses
the local registration port. ``--remote-port`` defaults to the effective
``--port``; a distinct value requires ``--ssh``. Both ports must be integers
from 1 to 65535. There are no platform, model, driver, scheduling or
interpreter options.

The command installs a pinned upstream report package in the managed
environment, verifies it, and binds the backend to ``127.0.0.1``.
SSH automatically opens a loopback-only local forward and uses a remote
pseudo-terminal so disconnecting the tunnel terminates the remote server.
After the backend's health endpoint responds, stdout contains the URL to
register in a frontend running on the local machine; provisioning and server
logs are on stderr. Keep the command running while browsing. Ctrl+C stops the
server and forwarding connection.

.. code-block:: sh

   qibocal-report-server --ssh ACCESS_NODE \
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

``KEY`` is ``agents.NAME.key`` or ``agents.NAME.report.key`` for named
settings, or ``section.key`` for the unnamed ``agent`` and ``report``
defaults, with underscore-separated setting names.
``path`` prints an absolute path without creating anything.
``show`` prints stored JSON only. ``get`` prints strings as plain text and
arrays/numbers as JSON; missing settings error.
``set`` writes sparse validated configuration atomically and creates parent
directories when needed. ``unset`` errors on missing settings and removes
empty sections.

Values for string settings are literal, not JSON-quoted. Use a JSON number for
``timeout``, JSON integers for ``report.port``/``report.remote_port``, and JSON
arrays for ``slurm_arg``, ``platform_repo`` and ``dependency``:

.. code-block:: sh

   qibocal-mcp-config set agent.platform YOUR_PLATFORM
   qibocal-mcp-config set agent.model openai:gpt-4.1
   qibocal-mcp-config set agent.venv /shared/qibocal-mcp/env
   qibocal-mcp-config set agent.platform_repo '["https://github.com/YOUR_ORG/platforms.git#ref=COMMIT"]'
   qibocal-mcp-config set agent.dependency '["YOUR_DRIVER_PACKAGE==VERSION"]'
   qibocal-mcp-config set agent.slurm_arg '["--account=quantum", "--time=00:15:00"]'

Set ``agent.partition`` before using that scheduling list at runtime.
``get`` returns these lists as JSON; ``unset`` restores their empty-list
defaults (or the unset default for ``venv``), subject to CLI overrides and the
SSH managed environment default.
The utility has no ``--no-config``, profile or clean command. Full key lists,
file-selection rules and error behavior are in :doc:`../guides/configuration`.
