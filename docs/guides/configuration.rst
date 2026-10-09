Persistent configuration
========================

File selection
--------------

Configuration is a JSON file named ``qibocal-mcp.json`` with optional
top-level ``server`` and ``agent`` objects, and no other top-level keys.
The default path is selected in this order:

1. If ``QIBOCAL_MCP_CONFIG_DIR`` is nonempty:
   ``$QIBOCAL_MCP_CONFIG_DIR/qibocal-mcp.json``.
2. Otherwise, if ``XDG_CONFIG_HOME`` is nonempty:
   ``$XDG_CONFIG_HOME/qibocal/qibocal-mcp.json``.
3. Otherwise: ``~/.config/qibocal/qibocal-mcp.json``.

Empty environment values fall through, as in qibocal-report. This reuses the
configuration folder but never accesses Qibocal Report's ``servers.json`` or
authentication files.

``qibocal-mcp`` and ``qibocal-agent`` both accept:

* ``--config FILE``: read only that file; an explicitly selected missing file
  is an error.
* ``--no-config``: ignore configuration completely.

These flags are mutually exclusive. A missing default file means there are
no stored defaults. Malformed JSON, unknown keys, unreadable files, invalid
types, empty or whitespace-only strings and invalid timeouts surface errors;
they never silently fall back to defaults. The whole selected file is
validated, including the section not used by the current command.

Settings and built-in defaults
------------------------------

Keys use underscores in JSON; the corresponding CLI flags use hyphens.
Every setting is optional in the file. ``platform`` and the agent's ``model``
must be supplied by configuration or CLI at runtime.

.. list-table:: Keys valid in both sections
   :header-rows: 1
   :widths: 18 20 62

   * - Key
     - Type
     - Built-in default and meaning
   * - ``platform``
     - Nonempty string
     - Unset; required at runtime. Installed platform name.
   * - ``output``
     - Nonempty string
     - ``runs``. Artifact output directory.
   * - ``platforms``
     - Nonempty string
     - Unset; leaves ``QIBOLAB_PLATFORMS`` in the execution environment alone.
   * - ``partition``
     - Nonempty string
     - Unset; direct execution rather than SLURM dispatch.
   * - ``workdir``
     - Nonempty string
     - Server: current directory for SLURM workers. Agent: unset, so no
       working-directory override.
   * - ``slurm_arg``
     - JSON array of nonempty strings
     - ``[]``. Additional ``srun`` options; a nonempty list requires an
       effective ``partition``.

.. list-table:: Section-specific keys
   :header-rows: 1
   :widths: 28 20 52

   * - Key
     - Type
     - Built-in default and meaning
   * - ``server.worker_python``
     - Nonempty string
     - Current Python interpreter; interpreter used by SLURM workers.
   * - ``agent.model``
     - Nonempty string
     - Unset; required at runtime. Pydantic AI model identifier or
       ``openwebui:MODEL_ID``; see :doc:`model-providers`.
   * - ``agent.model_base_url``
     - Nonempty string
     - Unset; endpoint override for ``openwebui:`` or ``github-copilot:``.
       OpenWebUI URLs include ``/api``. No credentials in URLs.
   * - ``agent.ssh``
     - Nonempty string
     - Unset; SSH host/alias when provided.
   * - ``agent.server_python``
     - Nonempty string
     - Unset; resolves to the current interpreter locally or ``python3``
       with SSH.
   * - ``agent.timeout``
     - Positive finite JSON number
     - ``3600`` seconds per operation, including queue waiting.

String values are not coerced from other types. ``null`` and booleans are not
valid settings; a timeout must be numeric, not a quoted string, and cannot
be zero, negative or nonfinite.
Use ``unset`` to remove a stored value and return to the built-in default
(unless explicitly overridden on the CLI); do not store ``null``.

Objectives and API keys are not valid configuration settings. Pass the
objective as the agent's positional argument and keep credentials in
environment/provider settings.

Precedence and isolation
------------------------

The order is **explicit CLI > command's own section > built-in default**.
There is no server-to-agent or agent-to-server inheritance. For example,
``server.platform`` does not satisfy the agent's required platform.

An explicit ``--slurm-arg`` list replaces the configured list rather than
appending to it. Repeated CLI occurrences form the replacement list:

.. code-block:: sh

   uv run qibocal-agent --slurm-arg=--account=ACCOUNT \
     --slurm-arg=--time=00:15:00 "YOUR_BOUNDED_OBJECTIVE"

Use the equals form for dashed values. A nonempty effective ``slurm_arg``
requires an effective partition, regardless of whether either came from the
file or CLI. Scheduling options must preserve the worker's stdio and
single-task lifecycle; see :doc:`networking`.

The agent launches its server with ``--no-config`` and resolved settings.
Target-host server configuration cannot inject settings. Internal SLURM
workers do not read user configuration.

Paths are literal
-----------------

Path settings are not expanded or rebased relative to the JSON file.
In particular, ``~`` and environment-variable strings in stored values are
not expanded. Relative ``output`` and ``workdir`` paths use the execution
host's current working directory; remote paths are interpreted remotely.
The agent's working-directory override determines where its server is launched.
The standalone server's ``workdir`` controls its SLURM worker directory,
not a directory change for direct execution.

Prefer absolute shared paths for SSH/SLURM. A Python executable setting should
name an executable, not a shell command such as ``uv run python``.

Manage defaults without hardware
--------------------------------

The dedicated command is:

.. code-block:: text

   qibocal-mcp-config [--config FILE] path|show|get KEY|set KEY VALUE|unset KEY

``KEY`` is ``section.key``, for example ``server.platform`` or ``agent.model``.
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
``timeout`` takes a JSON numeric value; ``slurm_arg`` takes a JSON array:

.. code-block:: sh

   uv run qibocal-mcp-config set server.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.model openai:gpt-4.1
   uv run qibocal-mcp-config set agent.partition PARTITION
   uv run qibocal-mcp-config set agent.timeout 7200
   uv run qibocal-mcp-config set agent.slurm_arg '["--account=quantum", "--time=00:15:00"]'
   uv run qibocal-mcp-config get agent.slurm_arg
   uv run qibocal-mcp-config unset agent.slurm_arg

For a separate project-specific file, put ``--config`` before the subcommand:

.. code-block:: sh

   uv run qibocal-mcp-config --config ./qibocal-mcp.json set agent.model openai:gpt-4.1
   uv run qibocal-mcp-config --config ./qibocal-mcp.json set agent.platform YOUR_PLATFORM
   uv run qibocal-agent --config ./qibocal-mcp.json "YOUR_BOUNDED_OBJECTIVE"

Sparse file example
-------------------

.. code-block:: json

   {
     "server": {
       "platform": "YOUR_PLATFORM",
       "output": "runs"
     },
     "agent": {
       "platform": "YOUR_PLATFORM",
       "model": "openai:gpt-4.1",
       "timeout": 3600
     }
   }

Do not add comments to the JSON. Omitting settings is how you request built-in
defaults; ``show`` will not fill them in. Storing scheduling arguments before
a partition is set does not make them usable at runtime: the merged runtime
settings must satisfy the partition requirement.
