The MCP servers
===============

The ``qibocal-mcp`` package connects any trusted MCP client to one Qibocal
calibration platform. The server makes no model API calls and requires no
model credentials; your client decides which experiments to run and whether
to accept their fitted updates.

One command serves two modes over stdio:

* ``qibocal-mcp --platform NAME`` — **direct mode**, run where the instruments
  are reachable: a lab workstation or an allocated SLURM compute node. It owns
  one persistent hardware connection.
* ``qibocal-mcp --platform NAME --partition QUEUE`` — **queued mode**, run on
  a SLURM access node: a hardware-free gateway runs each acquisition as its
  own ``srun`` job, so no allocation is held while results are inspected.

Both modes expose the same interface: nineteen tools —
``calibrate``, ``get_calibration_context``, ``get_protocol``,
``execute_protocol``, ``get_run``,
``cancel_run``,
``update_platform``, ``start_session``, ``get_platform_state``,
``search_platform_state``,
``patch_platform``, ``append_note``, ``get_notes``, ``get_run_results`` and
``get_run_figures`` (for the plot view only), ``list_versions``,
``diff_platform``, ``save_platform`` and ``checkout_platform`` — plus the
``qibocal://context`` resource and the ``calibrate`` MCP prompt.
The prompt distributes the complete calibration policy to the model; it is
also registered as the identically named tool for interfaces that load MCP
tools but ignore prompts (such as GitHub Copilot CLI today).
``get_calibration_context`` similarly aliases the context resource for
clients without resource support.

To start the server from a different machine than the one it runs on, use the
companion :doc:`qibocal-agent package <../agent/index>`: its
``qibocal-agent mcp`` command carries the same stdio interface over SSH and
can provision the server environment for you.

Complete the installation below, then configure your client's subprocess and
make a first tool call. Read :doc:`../guides/safety` before connecting to
instruments.

.. toctree::
   :maxdepth: 1

   installation
   getting-started

Detailed tool schemas are in :doc:`../reference/mcp`; command options are in
:doc:`../reference/cli`.
