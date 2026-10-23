Execution and accepted state
============================

Components
----------

The ``qibocal-mcp`` package contains the servers: the responsive frontend and
its main-thread hardware runtime (direct mode), the access-node SLURM gateway
and the one-operation compute-node worker. All of them register the shared
calibration policy from ``qibocal_mcp/calibration.py`` as the ``calibrate``
prompt and alias tool.

The ``qibocal-agent`` package contains the orchestration that launches and
reaches those servers: ``DeploymentSettings`` and ``launch_for`` in
``qibocal_agent/deployment.py`` build the local or SSH launch,
``qibocal_agent/environment.py`` provisions managed execution environments,
and ``qibocal-agent mcp`` hands the resulting stdio stream to any interactive
MCP client. ``qibocal-agent`` remains a one-shot autonomous CLI;
``open_calibration_agent`` keeps an MCP session alive for multiple Python
turns. Both fetch the server's ``calibrate`` prompt and context as their
instructions, manage their MCP subprocess, and leave strategy to the model:
there is no prescribed calibration sequence, fit acceptance threshold or
workflow engine.

``qibocal-mcp`` exposes nineteen tools, two resources (the context and the plot
view) and one prompt.
``get_calibration_context`` aliases ``qibocal://context`` for clients without
resource support; discovery through either interface has the same execution
effects. The ``calibrate`` prompt/tool is static and has none.
Its direct server wraps a Qibocal ``Executor`` with ``update=False``.
``execute_protocol`` binds a built-in protocol and calls the executor to acquire,
fit and generate reports. ``update_platform`` explicitly calls
``Executor.update(completed)``. No model API call is made by the server.
``get_platform_state`` inspects the paired models; ``patch_platform`` accepts
validated scalar edits and saves immutable snapshots for subsequent calls.
``execute_protocol(platform_overrides=...)`` uses isolated model copies for
one trial while retaining the accepted baseline and shared hardware connection.
``append_note`` and ``get_notes`` persist and inspect append-only session and
per-execution comment histories. Both agent runtimes share instructions to
record their plans and inspected findings; the server does not infer comments.

See :doc:`../reference/mcp` for the wire-facing interface and :doc:`safety`
for the limits of this separation.

Direct mode
-----------

With no partition, the server imports Qibocal and creates one calibration
platform in a background subprocess when its MCP session starts; it connects
to the instruments at the first acquisition. The MCP handshake and tool/resource
listing do not wait for this work. Other requests wait for initialization;
failures are logged on stderr and returned to those requests rather than retried.
The platform is created on that subprocess's main thread, as Qibolab installs
signal handlers then. Connection, acquisitions and disconnection run on one
dedicated hardware thread, so the server keeps answering during a run. The
frontend owns the subprocess and closes it when the MCP session ends.

Acquisitions run in the background, one at a time. ``execute_protocol`` waits
for its run up to ``wait`` seconds (45 by default, below common client request
timeouts), then returns the result, or the run's handle and progress;
``get_run`` waits again. A client timing out or cancelling a request never stops
a run; ``cancel_run`` does, before the run's next platform execution. While a run is in progress, notes and reads answer, while updates, edits,
session switches and other acquisitions are refused. Progress counts Qibolab's
platform executions and sums their duration estimates: a protocol may execute
many times, so the total is not known upfront. An accepted update changes the live platform used by
subsequent acquisitions and saves a snapshot under that run's output directory.
All executions share a standard Qibocal report under
``OUTPUT/sessions/<session_id>/`` with separate protocol/iteration folders.
Accepted updates also refresh the report's ``new_platform/``.
Report folders are created only when executing, restoring a run or explicitly
starting a calibration session, accepting explicit edits or appending session
comments, not during startup,
context discovery or state inspection.
``start_session`` replaces the active session's paired calibration models and
report folder while keeping the direct hardware connection. Prior execution
handles are cleared; prior files remain unchanged.

Environment provisioning still precedes the MCP server: a first managed launch
may need to install dependencies or clone platform repositories. SSH connection
setup and, in queued mode, SLURM allocation waits are separate sources of latency.

.. figure:: ../_images/local-workflow.svg
   :alt: An objective and platform context guide acquisition. The returned fit, table and image are inspected before an explicit update or rejection; follow-up acquisitions depend on the remaining budget.
   :width: 100%

   Acquisition and acceptance are separate decisions. Disabling automatic
   fitted updates does not prevent acquisition-side hardware changes.

Queued mode
-----------

With a partition, the server becomes a hardware-free access-node gateway.
Every worker receives the gateway's session ID and incrementally loads and
extends the same report, preserving execution order and timings.
Each operation runs in a fresh worker. Only acquisitions need instruments, so
only they are ``srun`` jobs; the other operations are software only and run in
a local worker on the access node, without waiting for an allocation:

1. Reading ``qibocal://context`` or calling ``get_calibration_context`` creates
   the platform, without connecting, and returns qubit IDs and protocol
   definitions.
2. Each ``execute_protocol`` job restores the latest accepted snapshot if one
   exists, connects, acquires/fits/reports, disconnects and exits. It runs in
   the background, as in direct mode; its status is ``queued`` until the job
   starts, with the partition's queue from ``squeue``, and its progress comes
   from the worker's stderr. The job is named ``qibocal-mcp-<run_id>``.
3. ``update_platform`` restores the saved execution for that run, applies the
   update and writes a snapshot.
4. ``patch_platform`` applies explicit edits, while ``get_platform_state``
   inspects the currently accepted snapshot. A first session comment pins the
   initial platform snapshot for subsequent jobs without accepting fitted
   changes.
5. Only a successful update or edit response advances the gateway's accepted snapshot.
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

Run handles exist only while the direct server or gateway remains alive and
expire when ``start_session`` switches the active calibration session.
Saved artifacts and client conversation history can persist, but cannot restore
those handles in a fresh MCP session; resuming a conversation in an interactive
client does not preserve the old server/gateway or accepted-state handoff.
``start_session`` can explicitly select saved paired platform files as the
baseline of a fresh calibration, but does not restore their old run handles.
Internal workers can restore the completed run needed for an update or a
run-based starting point within the active gateway session; this is not a
public execution-handle-resume feature.

The server itself reads no configuration file: every launch receives fully
resolved settings as flags from the orchestration, and internal SLURM workers
likewise do not read user configuration. This keeps a remote host's defaults
out of the chosen execution settings; see :doc:`configuration`.
