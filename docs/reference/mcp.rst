MCP reference
=============

Transport and session
---------------------

The server and queued gateway expose the same stdio interface, with exactly
four tools and one resource. Calls are serialized. Run handles belong to the
current calibration session and cannot be reused after switching sessions or
after the server/gateway exits.
Errors are exposed as MCP errors; failed acquisitions and updates are not
safe to retry blindly. See :doc:`../guides/safety`.

qibocal://context
-----------------

Read this resource or call ``get_calibration_context`` before choosing a
protocol, target or parameter.
Its text content is a JSON object containing:

* ``platform``: selected platform name.
* ``session_id`` and ``report_folder``: session identifier and execution-host
  path for the shared report, created on the first execution or an explicit
  ``start_session`` call.
* ``qubits``: platform qubit IDs.
* ``protocols``: built-in protocol names mapped to ``parameters`` (the
  parameter class source, including definitions/units), ``two_qubit_gates``
  and ``supports_update``.
* ``common_parameters``: explanatory text for ``nshots`` (integer) and
  ``relaxation_time`` (ns). Omit either to use platform defaults.

Protocol parameters are not a fixed universal schema; consult the installed
context. Discovery may connect to hardware; in queued mode, reading this
resource submits a discovery job and may wait for a queue allocation.

get_calibration_context()
-------------------------

This tool takes no arguments and returns exactly the same JSON text as
``qibocal://context``. It provides discovery for clients using tools rather
than resources, without executing a calibration protocol or applying fitted
updates. Choose protocols, targets and parameters from this context before
calling ``execute_protocol``.

The tool uses the same discovery path as the resource. It may connect to
hardware, and each call in queued mode submits a discovery job that may wait
for an allocation.

execute_protocol(protocol, parameters, targets)
-----------------------------------------------

.. list-table:: Arguments
   :header-rows: 1
   :widths: 24 20 56

   * - Argument
     - JSON type
     - Meaning
   * - ``protocol``
     - String
     - Built-in protocol name from context.
   * - ``parameters``
     - Object
     - Protocol-specific arguments and optional common acquisition settings.
   * - ``targets``
     - Array
     - Nonempty array of qubit IDs, or arrays of IDs for multi-qubit targets.

Single-qubit example:

.. code-block:: json

   {
     "protocol": "qubit_spectroscopy",
     "parameters": {"freq_width": 20000000, "freq_step": 200000, "nshots": 100},
     "targets": [0]
   }

Use IDs from your platform; they need not match this example.
For a multi-qubit protocol, a target pair is represented as a nested JSON
array, for example ``"targets": [[0, 1]]``. The server converts each nested
array to Qibocal's tuple-valued target. Multiple single-qubit targets such as
``[0, 1]`` are not the same as one pair ``[[0, 1]]``.

The tool binds the protocol and calls ``Executor.__call__()`` with automatic
fitted updates disabled. It acquires, fits and reports without applying
those fitted settings. Acquisition itself may still alter hardware state.

The response contains structured metadata plus text/image content:

* ``run_id``, platform, job ID, protocol, submitted parameters, targets and
  execution-host ``folder`` for this protocol execution.
* ``session_id`` and ``report_folder`` for the shared standard Qibocal report.
* Serialized fitted ``results`` when present, ``supports_update``, and
  initial ``updated``/``update_started`` flags.
* Text containing metadata and per-target report tables.
* PNG reports as MCP image content, not only file paths.

See :doc:`../guides/artifacts` for on-disk output. Some protocols lack results
or reports. In queued mode, each call is a fresh job that restores the latest
accepted platform snapshot and releases the allocation when done.
Report content comes directly from the protocol callback through
``Executor.report``; the bridge does not render a full report-page template.

update_platform(run_id)
-----------------------

The argument is the ``run_id`` string from an inspected execution in this
session:

.. code-block:: json

   {"run_id": "RUN_ID_FROM_EXECUTE_PROTOCOL"}

The tool requires fitted results and an update-capable protocol, then calls
``Executor.update(completed)`` explicitly. It updates the live platform and
saves a snapshot without overwriting the source platform definition.
It also refreshes the session report's ``new_platform/`` and metadata.
It returns ``run_id``, ``updated`` and the snapshot path; the queued gateway
forwards the worker response.

Each run's update can be attempted only once. An attempted update is blocked
from retry even if it failed after partially applying results. Updates are not
transactional. Older runs may be stale after other accepted updates.

In queued mode this is a separate job that restores the saved execution.
Only a successful response advances the gateway to the accepted snapshot,
including both Qibolab parameters and Qibocal calibration. Rejected fits are
never used as the next job's platform state.

start_session(source="current", run_id=None, ref=None, platform_path=None, snapshot_path=None)
--------------------------------------------------------------------------------------------

Start a fresh calibration within the connected agent/server, without restarting
the chat, reconnecting the MCP client or launching a second direct hardware
connection. Subsequent protocols are saved under a new
``OUTPUT/sessions/<session_id>/`` report, with iterations starting at zero.
The old reports and source platform files are left unchanged.

.. list-table:: Starting points
   :header-rows: 1
   :widths: 20 80

   * - ``source``
     - Meaning
   * - ``current`` (default)
     - Last successfully accepted update in this calibration session, or its
       baseline if no update was accepted. Unaccepted fits and partial failed
       updates are excluded.
   * - ``initial``
     - Baseline of the current calibration session, including any state selected
       by an earlier ``start_session`` call.
   * - ``run``
     - Require ``run_id`` from an execution in the current session. Restore the
       immutable state saved after that execution and apply its fitted results.
       Later updates are excluded, even if this run was updated later.
   * - ``repository``
     - Read committed ``parameters.json`` and ``calibration.json`` from ``ref``
       (branch, tag or commit; defaults to ``HEAD``). ``platform_path`` optionally
       selects the execution-host folder containing the platform files; otherwise
       the configured platform lookup is used.
   * - ``snapshot``
     - Require ``snapshot_path``: an execution-host folder containing paired
       ``parameters.json`` and ``calibration.json`` files. This can be a saved
       accepted run snapshot or another session's initial snapshot.

Only supply ``run_id`` for ``run``, ``ref``/``platform_path`` for ``repository``,
and ``snapshot_path`` for ``snapshot``. Examples:

.. code-block:: json

   {"source": "current"}
   {"source": "run", "run_id": "INSPECTED_RUN_ID"}
   {"source": "repository", "ref": "calibration-baseline"}
   {"source": "repository", "ref": "COMMIT_SHA", "platform_path": "/shared/platforms/my_platform"}
   {"source": "snapshot", "snapshot_path": "/shared/runs/sessions/SESSION_ID/platform"}

``source=run`` is an explicit fit-application decision: inspect and approve that
execution's images/results first, just as for ``update_platform``. It requires
fitted results and an update-capable protocol. Failed/incomplete update attempts
cannot be used as run starting points. Fitted results are applied only to the new
session's state; the original run's update flags and snapshots are not modified.

Repository selection reads Git objects locally on the execution host. It does
not fetch, checkout, reset, modify a dirty worktree or execute historical
``platform.py`` code. Both models must be compatible with the currently configured
hardware/drivers; this tool changes calibration state, not instrument topology.

The response contains the new ``session_id``, ``report_folder``, immutable initial
``snapshot`` path, ``source`` and ``parent_session_id``. Run/repository/snapshot
selection adds its provenance; repository refs are resolved to an exact
``commit``. Provenance is also saved in ``session.json``.
After success, all previous run handles expire: refresh
``get_calibration_context`` and use only new executions for updates. A rejected
starting point leaves the active session unchanged.

In queued mode the switch runs in a SLURM worker. Only a successful response
changes the gateway's session ID and accepted snapshot; later jobs load the new
baseline and write to the new folder.
