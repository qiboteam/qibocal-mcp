Standalone MCP server
=====================

Use ``qibocal-mcp`` to connect another trusted MCP client to one Qibocal
calibration platform. This track is independent of the autocalibration agent:
the server makes no model API calls and requires no model credentials or
agent extra.

The server exposes four tools: ``get_calibration_context``,
``execute_protocol``, ``update_platform`` and ``start_session``, plus the ``qibocal://context``
resource. The discovery tool aliases that resource for clients without resource
support. Your client decides which experiments to run and whether to accept
their fitted updates.
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
