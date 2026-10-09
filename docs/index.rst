qibocal-agent
=============

An autonomous calibration agent for Qibocal. The agent chooses experiments,
inspects report images and fitted results, and decides whether to accept
platform updates. Execution can be local or dispatched through SSH and SLURM.
Choose the one-shot Pydantic AI CLI, reusable Python sessions or interactive
GitHub Copilot CLI. Both integrations share calibration policy and deployment,
and manage their own stdio MCP subprocess; no separate server setup is needed.

The standalone MCP server is included for other trusted clients. Its basic
documentation is a separate track, independent of the agent instructions.

.. warning::

   This is a single-platform draft, not a production hardware safety layer.
   Model instructions are not enforced hardware limits. Read
   :doc:`guides/safety` before connecting to instruments.

Start with the :doc:`agent track <agent/index>` for autocalibration, or the
:doc:`MCP track <mcp/index>` to integrate another client. Both tracks put a
minimal setup first; detailed customization comes later.

.. toctree::
   :maxdepth: 2
   :caption: Autocalibration agent

   agent/index

.. toctree::
   :maxdepth: 2
   :caption: Standalone MCP server

   mcp/index

.. toctree::
   :maxdepth: 2
   :caption: Advanced guides

   guides/index

.. toctree::
   :maxdepth: 2
   :caption: Reference and development

   reference/index
   development
