MCP reference
=============

Transport and session
---------------------

The server and queued gateway expose the same stdio interface, with exactly
three tools and one resource. Calls are serialized. Run handles belong to the
current server/gateway session and cannot be reused after it exits.
Errors are exposed as MCP errors; failed acquisitions and updates are not
safe to retry blindly. See :doc:`../guides/safety`.

qibocal://context
-----------------

Read this resource or call ``get_calibration_context`` before choosing a
protocol, target or parameter.
Its text content is a JSON object containing:

* ``platform``: selected platform name.
* ``session_id`` and ``report_folder``: session identifier and execution-host
  path for the shared report, created on the first execution.
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
