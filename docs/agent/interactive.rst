Interactive use from any MCP client
===================================

``qibocal-mcp-remote`` serves the calibration MCP over stdio for interactive
clients — GitHub Copilot CLI, Claude Code or Desktop, Cursor, OpenCode, or any
other MCP-capable interface. It resolves the same settings as the agent
(configuration file, named agents, CLI flags), then launches the real server:
directly on this machine, or over SSH on the access node, provisioning its
managed environment there when required. Your conversation travels over stdio;
no HTTP service or port forwarding is involved.

With the settings from :doc:`getting-started` stored, register the command in
your client's MCP configuration:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "command": "qibocal-mcp-remote"
       }
     }
   }

or with explicit flags instead of stored settings:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "command": "qibocal-mcp-remote",
         "args": ["--platform", "YOUR_PLATFORM", "--ssh", "ACCESS_NODE",
                  "--partition", "PARTITION"]
       }
     }
   }

A positional name selects a configured named agent:
``qibocal-mcp-remote mylab``. No model settings are read — your client brings
its own model — and the server itself makes no model API calls.

Where to run what
-----------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Your client runs on
     - Configure it to launch
   * - The machine wired to the instruments, or an allocated compute node
     - ``qibocal-mcp --platform NAME`` (the server itself; the orchestration
       package is not needed)
   * - The SLURM access node
     - ``qibocal-mcp --platform NAME --partition QUEUE``, or
       ``qibocal-mcp-remote`` without ``--ssh`` to add managed provisioning
   * - Your laptop, with hardware behind SSH
     - ``qibocal-mcp-remote`` with ``--ssh`` (and ``--partition`` for SLURM
       dispatch)

Driving the calibration
-----------------------

Once connected, load the server's ``calibrate`` prompt — exposed as a slash
command or template by clients that support MCP prompts, and as the
``calibrate`` tool everywhere else (Copilot CLI currently ignores MCP prompts,
so its models pick the tool up from its "start here" description). The prompt
contains the full calibration policy; supply your objective, hardware limits,
budget and stopping criteria in the conversation.

Your client must be able to display the returned MCP report images — fit
acceptance decisions require inspecting actual pixels, not file paths. Set the
client's per-operation timeout generously in queued mode: it includes queue
waits. Noninteractive SSH authentication (keys or an agent) must be configured
beforehand; there is no password prompt.

To browse the accumulated reports alongside the conversation, run
``qibocal-report-server`` (optionally with the same agent name) and open the
printed URL; see :doc:`../reference/cli`.
