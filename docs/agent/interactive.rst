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

Switching configurations in the conversation
--------------------------------------------

With several setups stored (for example a simulator as the unnamed default,
and ``agents.platinum`` and ``agents.tuna`` for two chips), one registered
server covers all of them. Just ask, for instance *"list the qibocal
configurations"* or *"switch to the platinum configuration"*. The model uses
two extra tools:

* ``list_configurations`` shows every stored configuration, with platform,
  SSH host, partition and output folder, and which one is active.
* ``switch_configuration`` closes the active server and starts the selected
  one; the unnamed default is selected with no name.

Switching ends the previous calibration session: its run handles expire, so
record notes and finish or reject pending updates first. The first switch to
a configuration may provision its environment and take minutes. The
configuration file is read again on each switch, so a configuration added with
``qibocal-mcp-config`` mid-conversation can be used right away. Options given
on the command line apply only to the configuration chosen at startup.

If the starting configuration fails to launch, the server stays connected with
just these two tools: ask the model what went wrong (``list_configurations``
reports the error) and switch to a working configuration.

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
