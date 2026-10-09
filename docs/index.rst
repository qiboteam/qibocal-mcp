Qibocal MCP
===========

Qibocal MCP connects a trusted MCP client to one calibration platform.
The FastMCP server exposes acquisition and explicit platform updates; the
optional Pydantic AI agent chooses experiments, inspects report images and
decides what to do next. Execution can be local or dispatched through SSH
and SLURM, using stdio rather than an HTTP service.

.. warning::

   This is a single-platform draft, not a production hardware safety layer.
   Model instructions are not enforced hardware limits. Read
   :doc:`guides/safety` before connecting to instruments.

Start with :doc:`installation` and :doc:`getting-started`, then choose a
:doc:`tutorial <tutorials/index>`. For repeatable defaults, see
:doc:`guides/configuration`. Tool arguments and commands are documented in
:doc:`reference/index`.

.. toctree::
   :maxdepth: 2
   :caption: Start here

   installation
   getting-started
   tutorials/index

.. toctree::
   :maxdepth: 2
   :caption: Understand and configure

   guides/index

.. toctree::
   :maxdepth: 2
   :caption: Reference and development

   reference/index
   development
