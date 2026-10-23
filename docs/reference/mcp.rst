MCP reference
=============

Transport and session
---------------------

The server and queued gateway expose the same stdio interface, with exactly
seventeen tools, two resources (the calibration context and the interactive plot
view) and one prompt. Acquisitions run in the background, one at a time;
see :doc:`../guides/architecture`. Run handles
belong to the current calibration session and cannot be reused after switching
sessions or after the server/gateway exits.
Errors are exposed as MCP errors; failed acquisitions and updates are not
safe to retry blindly. See :doc:`../guides/safety`.

calibrate (prompt and tool)
---------------------------

The complete calibration policy — planning from discovered context, one
protocol at a time, report-image inspection, append-only note keeping and the
explicit update rules — is distributed by the server itself, registered twice:

* as the MCP **prompt** ``calibrate``, for interfaces that expose prompts as
  slash commands or templates;
* as the identically named **tool**, taking no arguments and returning the
  same text, for interfaces that load MCP tools but ignore prompts.

Both are static: they need no hardware initialization, never queue a SLURM
job and have no execution effects. The Pydantic AI agent fetches this prompt
and composes it with the context resource as its instructions, so every
integration follows a single server-defined policy. The text is guidance for
the model, not an enforced safety interlock.

qibocal://context
-----------------

Read this resource or call ``get_calibration_context`` before choosing a
protocol, target or parameter.
Its text content is a JSON object containing:

* ``platform``: selected platform name.
* ``session_id`` and ``report_folder``: session identifier and execution-host
  path for the shared report, created on the first execution, accepted edit,
  session comment or an explicit ``start_session`` call.
* ``qubits``: platform qubit IDs.
* ``protocols``: built-in protocol names mapped to ``parameters`` (the
  parameter class source, including definitions/units), ``two_qubit_gates``
  and ``supports_update``.
* ``common_parameters``: explanatory text for ``nshots`` (integer) and
  ``relaxation_time`` (ns). Omit either to use platform defaults.

Protocol parameters are not a fixed universal schema; consult the installed
context. Discovery creates the platform without connecting to the instruments,
and needs no allocation in queued mode.

get_calibration_context()
-------------------------

This tool takes no arguments and returns exactly the same JSON text as
``qibocal://context``. It provides discovery for clients using tools rather
than resources, without executing a calibration protocol or applying fitted
updates. Choose protocols, targets and parameters from this context before
calling ``execute_protocol``.

The tool uses the same discovery path as the resource.

get_platform_state(path="")
---------------------------

Inspect the live Qibolab ``Parameters`` and Qibocal ``Calibration`` models,
without acquiring data or applying updates. The response contains
``session_id``, ``report_folder``, ``path`` and ``value``. With the default empty
path, ``value`` contains both complete models under ``parameters`` and
``calibration``. Inspection alone creates no report folder. Like context discovery,
it waits for hardware initialization and submits a worker job in queued mode.

Select a subtree or scalar using an RFC 6901 JSON pointer. Keys are strings in
this serialized representation, even for numeric qubit IDs. Escape ``/`` within
a key as ``~1`` and ``~`` as ``~0``; array elements use zero-based indices.
Missing paths and malformed pointers are errors. For example, if your inspected
platform has a channel named ``0/drive``:

.. code-block:: json

   {"path": "/parameters/configs/0~1drive/frequency"}
   {"path": "/calibration/single_qubits/0/qubit/frequency_01"}

patch_platform(changes)
-----------------------

Accept explicit platform edits for the rest of this session, independently of
any protocol's fitted update callback. ``changes`` is a nonempty object mapping
existing JSON pointers to replacement scalar values (including null).
Only individual values within ``parameters`` or ``calibration`` can be replaced:
adding/removing keys, replacing containers and overlapping paths are rejected.
Inspect current state first; paths and units depend on the installed platform.

For a platform with these inspected paths, a coupled frequency edit could be:

.. code-block:: json

   {
     "changes": {
       "/parameters/configs/0~1drive/frequency": 5000000000,
       "/calibration/single_qubits/0/qubit/frequency_01": 5000000000
     }
   }

Both models are validated together before changing either live model; validation
errors leave the session unchanged. The server does not infer relationships
between hardware configurations and calibration fields: supply coupled edits
together. Model validation is not a hardware safety interlock.

The response contains ``session_id``, ``report_folder``, ``edit_id``, submitted
``changes``, ``updated`` and an immutable ``snapshot`` path. Paired model files
and ``edit.json`` provenance are saved under ``edits/<edit_id>/`` in the session
report. The original baseline and source platform files are not overwritten.
Successful edits refresh ``new_platform/`` and are used by subsequent protocols
and ``start_session(source="current")``. ``source="initial"`` excludes edits
accepted after the current session's baseline.

In queued mode each edit is a separate job. Only a successful response advances
the gateway to the immutable edit snapshot. Disk errors are surfaced, not
reported as success; they may leave partial report artifacts.

Dedicated tools are intentional: a disk edit would not change a direct server's
in-memory models, and the next queued job restores the gateway's accepted
snapshot rather than arbitrary modified files. Native agent profiles also
provide no general filesystem writer. These tools therefore enable autonomous
lookup and exploration without adding broad filesystem permissions.

execute_protocol(protocol, parameters, targets, platform_overrides=None)
------------------------------------------------------------------------

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
   * - ``platform_overrides``
     - Object or null
     - Optional scalar platform edits for this execution only, using the same
       pointer/value format and validation as ``patch_platform``.
   * - ``wait``
     - Number
     - Seconds to wait for the result (default 45). A longer run continues in
       the background and returns its status instead.

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

``platform_overrides`` runs the protocol against isolated copies of both models.
The accepted session models and ``new_platform/`` remain unchanged, including
when the trial fails. The run's ``execution_platform/`` records the actual trial
state, and ``platform_overrides`` is included in run metadata. Subsequent jobs
load the accepted baseline, never the trial checkpoint.
This is software-state isolation, not physical hardware rollback.

``update_platform`` still applies only the protocol's fitted callback to the
current live state; it does not promote trial overrides automatically.
Use ``patch_platform`` to accept chosen explicit values, or
``start_session(source="run")`` to start from the trial checkpoint and apply
its inspected fitted results in a new session.

The response is kept small, so that clients keep it in the model's context. It
contains, in order:

* A note on showing the plots to the user (see below).
* Text with the run metadata, also returned as structured content:
  ``run_id``, platform, job ID, protocol, submitted parameters, targets,
  execution-host ``folder``, ``session_id`` and ``report_folder``,
  ``supports_update``, initial ``updated``/``update_started`` flags, and
  ``plots``: per figure, its ``target``, ``image`` path and whether it is
  ``interactive``.
* The serialized fitted ``results``, where values larger than 2 kB, typically
  per-shot or per-point arrays, are replaced by a note: request them with
  ``get_run_results``. ``run.json`` and ``results.json`` keep them in full.
* Per target, its report table and PNG reports as MCP image content.

A run outlasting ``wait`` returns its status instead: ``run_id``, ``status``
(``queued`` while a SLURM job waits for its allocation, then ``running``),
``protocol``, ``targets``, ``elapsed_s``, and ``executions`` and ``estimated_s``,
the platform executions so far and the sum of Qibolab's duration estimates for
them. While queued, ``queue`` describes the partition from ``squeue``: the job's
``job_id``, SLURM ``state`` and pending ``reason``; the numbers of ``running``
and other ``pending`` jobs, and ``pending_ahead``, those with higher priority or
submitted earlier; and, when at most five jobs run, ``running_jobs`` with their
ID, user, name, elapsed time and time limit. If ``squeue`` fails, ``queue``
holds its ``error`` instead. Clients asking for progress also receive it as MCP progress
notifications. While the run is in progress, another acquisition, an update, an
edit or a session switch is refused.

See :doc:`../guides/artifacts` for on-disk output. Some protocols lack results
or reports. In queued mode, each call is a fresh job that restores the latest
accepted platform snapshot and releases the allocation when done.

get_run(run_id, wait=45)
------------------------

Wait up to ``wait`` seconds for a run started by ``execute_protocol``, then
return its result, exactly as ``execute_protocol`` would have, or its status
again if still in progress. A failed run returns its error. Waiting has no
effect on the run: a request timing out or cancelled leaves it running.

cancel_run(run_id, wait=45)
---------------------------

Stop a run started by ``execute_protocol``, then wait up to ``wait`` seconds for
it to stop, returning its status. In direct mode, the run stops before its next
platform execution and reports ``cancelling`` until then; in queued mode, the
job is terminated. A stopped run reports ``cancelled`` through ``get_run``. It
saves no data or fitted results and cannot be updated; hardware effects of the
executions already played are not rolled back. Cancelling a finished run is an
error.
Report content comes directly from the protocol callback through
``Executor.report``; the bridge does not render a full report-page template.

Interactive plots (MCP Apps)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``execute_protocol`` and ``get_run`` declare the ``ui://qibocal/plots.html``
view (the `MCP Apps <https://modelcontextprotocol.io/extensions/apps/overview>`_
extension). Chat interfaces supporting it, such as VS Code GitHub Copilot (see
the `client matrix <https://modelcontextprotocol.io/extensions/client-matrix>`_),
render the run's plots there, next to the tool call. The view fetches each
figure's Plotly JSON on demand through ``get_run_figures``, a tool reserved to
it (``visibility: ["app"]``), and draws it interactively; figures heavier than
200 kB, such as raw single shots, are shown as their PNG instead. It loads
plotly.js from ``https://cdn.plot.ly``, the only origin its content security
policy declares. The PNG images are what the model inspects in every case.

The note opening each result with plots depends on whether the client declared
the MCP Apps capability during initialization, the only signal the server
relies on. With it, the model is told that the interface shows the plots and
not to embed them again; without it, that the user cannot see tool results,
and to embed the listed images in its next message, before any other tool call.
Each server in a chain (runtime, frontend, gateway, ``qibocal-agent mcp``)
rewrites the note for its own client, so the one facing the user's interface
decides.

get_run_results(run_id, fields=None)
------------------------------------

Return a completed run's fitted results in full, for the rare decision needing
values left out of the response. Without ``fields``, it lists the available
fields with their sizes in bytes; with ``fields``, plain names such as
``["fidelity"]``, it returns those values.

get_notes(run_id=None)
-----------------------

Read the current session's comment history, or one protocol execution's history
by supplying the ``run_id`` returned by ``execute_protocol``. Repeated protocol
executions have independent histories. The response contains ``session_id``,
``report_folder``, ``run_id`` (null for session notes) and ``notes`` in append
order. Each note has ``content``, a timezone-aware UTC ``timestamp`` and
``author`` (possibly null). Missing notes files mean an empty history;
malformed stored histories are errors, not silently discarded.
Reading session notes alone does not create a report folder.

append_note(content, run_id=None, author=None)
----------------------------------------------

Append a comment to the current session, or to one protocol execution using its
``run_id``. ``content`` must be nonblank; surrounding whitespace is stripped.
The server generates the UTC timestamp. ``author`` is an optional descriptive
label supplied by the caller, **not authenticated identity**; this stdio server
does not authenticate authors. The response has the same fields as
``get_notes`` and includes the complete history after appending.

.. code-block:: json

   {"content": "Plan: inspect spectroscopy before accepting a frequency update.", "author": "calibration-agent"}
   {"content": "Fit rejected: resonance is at the sweep boundary.", "run_id": "RUN_ID_FROM_EXECUTE_PROTOCOL", "author": "calibration-agent"}
   {"content": "Follow-up: widen the next sweep.", "run_id": "RUN_ID_FROM_EXECUTE_PROTOCOL"}

Comments are append-only through MCP. Append follow-up findings rather than
replace earlier conclusions. Session comments live in ``notes.json`` at the
report root; execution comments live in ``data/<protocol>-<iteration>/notes.json``.
Appending a session comment creates the report and initial platform snapshot
if necessary, but changes no accepted calibration state, fitted results, update
flags or acquisition data. It does not approve a fit or execute an update.
Later acquisitions, edits and updates leave comment files untouched.

Writers use the same per-directory ``.notes.lock`` as the report server and
atomically replace only ``notes.json``, preserving concurrent report UI appends.
Other writers must honor that lock; Qibocal's generic ``Output.dump`` and
``dump_notes`` APIs alone do not coordinate concurrent writes.
Notes and lock symlinks are rejected. Persistence errors are surfaced.

Both tools wait for hardware initialization in direct mode and submit a worker
job in queued mode, including a platform connection but no acquisition.
Only run handles from the active session are accepted. After ``start_session``,
the new session starts with empty histories; the old report's notes remain.
Both agent integrations are instructed to record plans, inspected findings,
update decisions, follow-ups and final session summaries through these tools.
The server itself does not infer findings or make model calls.

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

Platform versions
-----------------

``list_versions(ref=None, limit=20, fetch=False, remote="origin")``,
``diff_platform(against="source")``, ``save_platform(message, branch,
confirm=None, push=False, remote=None)`` and ``checkout_platform(ref)`` list,
compare, save and check out platform versions; see :doc:`../guides/versions`
for the workflow and its guarantees. Differences list changed values by JSON
pointer, with ``old`` and ``new`` values (``null`` when absent; values above
2 kB are summarized by their size), at most 200 of them. In queued mode, they
run in local workers on the access node, without an allocation.

start_session(source="current", run_id=None, ref=None, platform_path=None, snapshot_path=None, fetch=False, remote=None)
------------------------------------------------------------------------------------------------------------------------

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
     - Last successfully accepted fitted update or explicit edit in this calibration session, or its
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
       the configured platform lookup is used. ``fetch=true`` first retrieves
       the selected remote revision; ``remote`` names an existing Git remote
       (defaults to ``origin`` when fetching).
   * - ``snapshot``
     - Require ``snapshot_path``: an execution-host folder containing paired
       ``parameters.json`` and ``calibration.json`` files. This can be a saved
       accepted run snapshot or another session's initial snapshot.

Only supply ``run_id`` for ``run``, ``ref``/``platform_path``/``fetch``/``remote``
for ``repository``, and ``snapshot_path`` for ``snapshot``. ``remote`` requires
``fetch=true``. Examples:

.. code-block:: json

   {"source": "current"}
   {"source": "run", "run_id": "INSPECTED_RUN_ID"}
   {"source": "repository", "ref": "calibration-baseline"}
   {"source": "repository", "ref": "COMMIT_SHA", "platform_path": "/shared/platforms/my_platform"}
   {"source": "repository", "fetch": true}
   {"source": "repository", "fetch": true, "remote": "origin", "ref": "main"}
   {"source": "snapshot", "snapshot_path": "/shared/runs/sessions/SESSION_ID/platform"}

``source=run`` is an explicit fit-application decision: inspect and approve that
execution's images/results first, just as for ``update_platform``. It requires
fitted results and an update-capable protocol. Failed/incomplete update attempts
cannot be used as run starting points. Fitted results are applied only to the new
session's state; the original run's update flags and snapshots are not modified.

Repository selection stays offline unless ``fetch=true``. Fetching reads a
branch, tag or commit from a remote already configured in that repository;
the agent cannot supply a new URL or a destination refspec. With fetching enabled,
an omitted ``ref`` (or ``HEAD``) selects the remote's HEAD, not the local HEAD.
Use remote branch names such as ``main``, not local tracking names such as
``origin/main``. Git authentication and network access must already work
noninteractively on the execution host (the compute node in queued mode).

Fetching downloads Git objects and writes ``FETCH_HEAD`` but does not advance
local branches, tags or remote-tracking refs, pull, checkout, reset, modify a dirty
worktree or execute historical ``platform.py`` code. It does not reinstall
dependencies or reload drivers. Both models must be compatible with the currently
configured hardware/drivers; this tool changes calibration state, not instrument
topology. Fetch or model-validation failures leave the active session unchanged,
though downloaded Git objects may remain.

The response contains the new ``session_id``, ``report_folder``, immutable initial
``snapshot`` path, ``source`` and ``parent_session_id``. Run/repository/snapshot
selection adds its provenance; repository refs are resolved to an exact
``commit``. Fetched revisions also include ``fetch: true`` and the selected
``remote``. Provenance is also saved in ``session.json``.
After success, all previous run handles expire: refresh
``get_calibration_context`` and use only new executions for updates. A rejected
starting point leaves the active session unchanged.

In queued mode the switch runs in a SLURM worker. Only a successful response
changes the gateway's session ID and accepted snapshot; later jobs load the new
baseline and write to the new folder.
