Command-line reference
======================

Commands below are installed by the project and can be launched with ``uv run``.
Use ``--help`` on a command for its argument parser. Stored defaults, validation
and path semantics are detailed in :doc:`../guides/configuration`.

Common runtime options
----------------------

Both ``qibocal-agent`` and ``qibocal-mcp`` accept:

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
``$XDG_CACHE_HOME/qibocal-mcp/envs`` or ``~/.cache/qibocal-mcp/envs``.
Other existing path options remain literal. Repository fragments must have
URL-encoded values as needed; quote arguments containing ``&``.
Cached clones are reused without automatic pull/fetch/reset; pin a commit for
reproducibility. Without a managed environment, repository/dependency options
intentionally modify the selected existing interpreter. Use ``--venv`` for
isolation and trusted sources only. See :doc:`../guides/configuration` for
lookup precedence, dependency discovery and cache/reconciliation semantics.

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
                 [--slurm-arg VALUE ...] [--timeout SECONDS] OBJECTIVE

.. list-table:: Agent-only options and argument
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
       queue wait; not an entire-session budget.
   * - ``OBJECTIVE``
     - Required positional calibration goal, hardware limits, execution budget
       and stopping criteria. Cannot be stored in configuration.

The agent owns one server subprocess and supplies resolved settings with
``--no-config``. It has no ``--worker-python`` option; queued workers use the
server interpreter. Model credentials remain in environment/provider settings.
The provider is resolved before deployment or hardware access. SSH managed
bootstrap transfers the launching installation's server source and metadata
(from a checkout or installed distribution), not configuration or model keys;
target-host configuration is not read. Managed environments check dependencies
and import the server before launch.

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

qibocal-mcp-config
------------------

.. code-block:: text

   qibocal-mcp-config [--config FILE] path
   qibocal-mcp-config [--config FILE] show
   qibocal-mcp-config [--config FILE] get KEY
   qibocal-mcp-config [--config FILE] set KEY VALUE
   qibocal-mcp-config [--config FILE] unset KEY

``KEY`` is ``section.key`` with underscore-separated setting names.
``path`` prints an absolute path without creating anything.
``show`` prints stored JSON only. ``get`` prints strings as plain text and
arrays/numbers as JSON; missing settings error.
``set`` writes sparse validated configuration atomically and creates parent
directories when needed. ``unset`` errors on missing settings and removes
empty sections.

Values for string settings are literal, not JSON-quoted. Use a JSON number for
``timeout`` and JSON arrays for ``slurm_arg``, ``platform_repo`` and
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
