Standalone MCP server
=====================

Use ``qibocal-mcp`` to connect another trusted MCP client to one Qibocal
calibration platform. This track is independent of the autocalibration agent:
the server makes no model API calls and requires no model credentials or
agent extra.

The server exposes eight tools: ``get_calibration_context``,
``execute_protocol``, ``update_platform``, ``start_session``,
``get_platform_state``, ``patch_platform``, ``append_note`` and ``get_notes``,
plus the ``qibocal://context``
resource. The discovery tool aliases that resource for clients without resource
support. Your client decides which experiments to run and whether to accept
their fitted updates. Structured inspection and explicit session edits also
support agent-inferred values; per-execution platform overrides allow isolated
trials without accepting those values for later experiments.
Comment tools retain session plans and per-execution findings in the report,
including follow-up comments without replacing earlier conclusions.
Communication is over stdio, locally or over SSH, not an HTTP service.

Complete the installation below, then configure your client's subprocess
and make a first tool call. Read :doc:`../guides/safety` before connecting
to instruments.

.. toctree::
   :maxdepth: 1

   installation
   getting-started

Detailed tool schemas are in :doc:`../reference/mcp`; runtime options and
stored defaults are in :doc:`../reference/cli` and
:doc:`../guides/configuration`.
