Execution and accepted state
============================

Components
----------

``qibocal-agent`` owns one MCP subprocess for a calibration session.
It reads the platform context and gives the model the objective, protocol
definitions and an MCP toolset. The model chooses a strategy; there is no
prescribed calibration sequence, fit acceptance threshold or workflow engine.

``qibocal-mcp`` exposes exactly two tools and one context resource.
Its direct server wraps a Qibocal ``Executor`` with ``update=False``.
``execute_protocol`` binds a built-in protocol and calls the executor to acquire,
fit and generate reports. ``update_platform`` explicitly calls
``Executor.update(completed)``. No model API call is made by the server.

See :doc:`../reference/mcp` for the wire-facing interface and :doc:`safety`
for the limits of this separation.

Direct mode
-----------

With no partition, the server creates and connects one calibration platform
when its MCP session starts. It disconnects when the session ends.
Calls are serialized. An accepted update changes the live platform used by
subsequent acquisitions and saves a snapshot under that run's output directory.

.. figure:: ../_images/local-workflow.svg
   :alt: An objective and platform context guide acquisition. The returned fit, table and image are inspected before an explicit update or rejection; follow-up acquisitions depend on the remaining budget.
   :width: 100%

   Acquisition and acceptance are separate decisions. Disabling automatic
   fitted updates does not prevent acquisition-side hardware changes.

Queued mode
-----------

With a partition, the server becomes a hardware-free access-node gateway.
The gateway serializes operations and launches ``srun`` workers:

1. Reading ``qibocal://context`` launches a discovery job, including a platform
   connection, then returns qubit IDs and protocol definitions.
2. Each ``execute_protocol`` job restores the latest accepted snapshot if one
   exists, connects, acquires/fits/reports, disconnects and exits.
3. ``update_platform`` launches a separate job. It restores the saved execution
   for that run, applies the update and writes a snapshot.
4. Only a successful update response advances the gateway's accepted snapshot.
   Subsequent jobs restore both ``parameters.json`` and ``calibration.json``.

.. figure:: ../_images/queued-workflow.svg
   :alt: SSH stdio connects the laptop agent to an access-node gateway. Separate discovery, acquisition and update jobs use shared storage. Only accepted platform snapshots feed future jobs; an inspected but rejected fit is not handed off as platform state.
   :width: 100%

   The gateway retains session handles and the latest accepted snapshot path,
   not an idle hardware allocation.

Report inspection happens between jobs, with no allocation held for the model.
Rejected fits never become the next job's platform state. An update failure
can nevertheless have partially affected hardware; accepted-state handoff
does not make updates transactional.

Session boundaries
------------------

Run handles exist only while the direct server or gateway remains alive.
Saved artifacts persist, but resuming an entire agent session is not supported.
Internal workers can restore the completed run needed for an update within
the active gateway session; this is not a public session-resume feature.

Every server subprocess launched by the agent receives ``--no-config`` and
resolved settings. Internal SLURM workers likewise do not read user
configuration. This keeps a remote host's defaults out of the agent's chosen
execution settings; see :doc:`configuration`.
