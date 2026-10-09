Command-line reference
======================

Commands below are installed by the project and can be launched with ``uv run``.
Use ``--help`` on a command for its argument parser. Stored defaults, validation
and path semantics are detailed in :doc:`../guides/configuration`.

Common runtime options
----------------------

Both ``qibocal-mcp`` and ``qibocal-agent`` accept:

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
     - Set ``QIBOLAB_PLATFORMS`` on the execution host; unset preserves its
       environment value.
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

qibocal-mcp
-----------

.. code-block:: text

   qibocal-mcp [--config FILE | --no-config] [--platform NAME]
               [--output PATH] [--platforms PATH] [--partition NAME]
               [--worker-python EXECUTABLE] [--workdir PATH]
               [--slurm-arg VALUE ...]

``--worker-python`` selects the SLURM worker interpreter and defaults to the
current Python interpreter. Direct mode does not launch workers.

The command runs a single-platform stdio server or queued gateway; it takes
no objective, model or SSH option, and has no ``--timeout`` option.
Other clients launch it as their subprocess and configure their own timeouts.
No model API call is made by this command.

qibocal-agent
-------------

.. code-block:: text

   qibocal-agent [--config FILE | --no-config] [--model MODEL]
                 [--model-base-url URL]
                 [--platform NAME] [--output PATH] [--platforms PATH]
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
     - Server interpreter. If unset, the current interpreter locally or
       ``python3`` with SSH. Remote paths are interpreted remotely.
   * - ``--timeout SECONDS``
     - Positive finite number, default 3600. Per-operation timeout including
       queue wait; not an entire-session budget.
   * - ``OBJECTIVE``
     - Required positional calibration goal, hardware limits, execution budget
       and stopping criteria. Cannot be stored in configuration.

The agent owns one server subprocess and supplies resolved settings with
``--no-config``. It has no ``--worker-python`` option; queued workers use the
server interpreter. Model credentials remain in environment/provider settings.

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
``timeout`` and a JSON array for ``slurm_arg``:

.. code-block:: sh

   uv run qibocal-mcp-config set server.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.model openai:gpt-4.1
   uv run qibocal-mcp-config set agent.slurm_arg '["--account=quantum", "--time=00:15:00"]'

Set ``agent.partition`` before using that scheduling list at runtime.
The utility has no ``--no-config``, profile or clean command. Full key lists,
file-selection rules and error behavior are in
:doc:`../guides/configuration`.
