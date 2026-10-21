Qibocal MCP servers
===================

Qibocal calibration for AI agents, split into two Python packages:

* **qibocal-mcp** — the MCP servers. One command, ``qibocal-mcp``, exposes
  Qibocal calibration tools and the reusable ``calibrate`` prompt over stdio.
  Run it where the instruments are reachable (a lab machine or a SLURM compute
  node), or on a SLURM access node with ``--partition`` to dispatch one job
  per operation. Install it on the execution host; connect any MCP-capable
  interface.
* **qibocal-agent** — the orchestration, one command with a subcommand per
  task. It reaches those servers from anywhere: ``qibocal-agent mcp`` starts
  the server through SSH (provisioning a managed Python environment when
  needed) and serves it to your local MCP client; ``qibocal-agent run`` runs
  an autonomous calibration agent on top; ``qibocal-agent report`` browses the
  generated reports; ``qibocal-agent config`` stores your configurations so a
  launch reduces to a name.

If you work directly on the machine that reaches the hardware, the MCP package
alone is enough. Install the orchestration package on your own machine when the
hardware is behind SSH or SLURM, or when you want the autonomous agent.

.. warning::

   This is a single-platform draft, not a production hardware safety layer.
   Model instructions are not enforced hardware limits. Read
   :doc:`guides/safety` before connecting to instruments.

.. toctree::
   :maxdepth: 2
   :caption: qibocal-mcp — the MCP servers

   mcp/index

.. toctree::
   :maxdepth: 2
   :caption: qibocal-agent — orchestration and agent

   agent/index

.. toctree::
   :maxdepth: 2
   :caption: Advanced guides

   guides/index

.. toctree::
   :maxdepth: 2
   :caption: Reference and development

   reference/index
   development
