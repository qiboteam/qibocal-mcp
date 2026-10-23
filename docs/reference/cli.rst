Command-line reference
======================

Each package installs one command. ``qibocal-mcp`` is the server itself and
takes only options. ``qibocal-agent`` groups the orchestration tasks as
subcommands. Both print their usage with ``--help``, on the command and on
every subcommand, and their manual pages are built with ``uv run poe man``
(see :doc:`../development`).

The option listings below are generated from the commands themselves.
Stored settings, their validation and the meaning of paths are described in
:doc:`../guides/configuration`.

qibocal-mcp
-----------

The server reads no configuration file: every setting is an option, so the
command can be registered as is in any MCP client. Without ``--partition``,
it connects the hardware directly; with it, it becomes a hardware-free
gateway that runs every acquisition as its own SLURM job. Long acquisitions
continue in the background, so client request timeouts need not cover them. No model API call
is made by this command.

.. click:: qibocal_mcp.server:main
   :prog: qibocal-mcp

qibocal-agent
-------------

Every subcommand selects a stored configuration by its optional ``NAME``,
``default`` when omitted. Options given on the command line override the
stored settings; repeatable options replace the stored list as a whole.
``--config FILE`` and ``--no-config`` select the configuration file and are
written before the subcommand, for example
``qibocal-agent --config ./agents.json mcp lab``.

``run`` takes a model and an objective, which it may also read from the
configuration; a single argument is a configuration name if one is stored
with it, and the objective for the default configuration otherwise. ``mcp``
needs no model: the interactive assistant brings its own, and stored
configurations can be listed and switched from the conversation. ``report``
serves the reports where they are, through a private SSH tunnel when remote,
and prints the address to register in the Qibocal Report interface. ``config``
manages the file: settings are addressed as ``NAME.setting`` or
``NAME.report.setting``; text values are taken literally, and lists and numbers
as JSON.

.. click:: qibocal_agent.cli:main
   :prog: qibocal-agent
   :nested: full
