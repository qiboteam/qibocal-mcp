Safety, limits and recovery
===========================

Trusted-agent draft
-------------------

This is a single-platform draft for a trusted agent, not a production hardware
safety layer. Natural-language limits and budgets guide the model but are not
enforced instrument interlocks or a deterministic workflow engine.
Use site-approved hardware protections, exclusive access and operator oversight.
Every integration retains its own permission behavior: the Pydantic AI agent's
defaults and whatever approvals your interactive MCP client applies. No new
safety or approval gate or blanket automatic permission allowance is added,
and the ``calibrate`` prompt's instructions do not enforce hardware bounds.

Put explicit amplitude/frequency limits, shot limits, an execution budget and
stopping criteria in every objective. Choose a vision-capable model and require
inspection of the actual tables and images. There is no built-in fit acceptance
threshold. Never treat synthetic/mock data as evidence of a successful
hardware calibration.
The selected model and client must support report images: the server returns
PNG reports as inline MCP image content. If your interface drops inline MCP
images or the model cannot inspect them, stop rather than accepting an update
from text alone. A path, table or successful tool status is not visual
evidence, and remote artifact paths are not local files.

Discovery is not necessarily hardware-free. Direct-mode startup connects the
platform; reading ``qibocal://context`` or calling ``get_calibration_context``
in queued mode launches a compute job that connects hardware. Establish
operator-approved bounds before discovery, including in a blank interactive
session. Only the ``calibrate`` prompt/tool is static and execution-free.

Acquisition versus fitted updates
---------------------------------

Automatic *fitted* platform updates are disabled. A protocol's acquisition can
still alter hardware state. A rejected fit or a failed call does not prove that
the instrument was untouched.

``update_platform`` is explicit and can only apply a run with fitted results
and a protocol supporting updates. Once a previous update changes the platform,
older runs can be stale; re-evaluate their relevance before accepting them.
Source platform files remain untouched, but the live platform and hardware
may have changed.

Explicit edits and temporary trials
------------------------------------

``get_platform_state`` returns serialized parameters and calibration without
acquisition, but like discovery it may connect hardware. ``patch_platform``
accepts explicit values independently of fitted callbacks; require inspected
evidence or explicit operator instructions before accepting them.
The agent must respect approved hardware limits and edit coupled hardware and
calibration fields together. Model validation does not enforce site-specific
bounds or guarantee that those fields are physically consistent.

``execute_protocol(platform_overrides=...)`` isolates both software models for
a single trial, including on failure. Accepted session state is not changed.
It does not undo physical acquisition-side effects, instrument commands or
partial failures. Neither trials nor saved snapshots are hardware rollback.
Older fitted runs can be stale after explicit edits as well as fitted updates.

Updates are not transactional
-----------------------------

Once an update is attempted, retries of that run are blocked, even after
failure, to avoid repeating a partial update. The implementation records
``update_started`` before applying results and records ``updated`` only after
the update and snapshot save complete.

An error can mean a partially applied change or missing snapshot, not an
unchanged device. Do not blindly retry acquisitions or failed/partially applied
updates. Stop, inspect retained metadata and logs, check instrument state and
use your site's recovery procedure.

Serialized calls do not provide a multi-user lock across independent server
processes. Coordinate hardware ownership outside this project.

Timeout and cancellation
------------------------

The agent's positive finite ``--timeout`` defaults to 3600 seconds per
operation and includes queue wait and execution. It is not an overall wall-clock
budget for model reasoning or the full objective. Scheduler ``--time`` requests
have their own meaning.

Queue/subprocess failures are surfaced to the model as errors. A request timing
out or being cancelled never stops an acquisition, which continues in the
background: follow it with ``get_run`` rather than submitting it again.

``cancel_run`` stops a run on purpose. In direct mode, the run stops right
before its next platform execution: the execution in progress is never
interrupted, so a protocol consisting of one long execution only stops at its
end. In queued mode, the gateway terminates the job's local ``srun``
controller, as it also does when it exits during a run; cleanup
waits up to ten seconds before killing a controller that has not exited.
SLURM/site signal handling governs how quickly the allocation and remote work
actually stop. Do not assume that closing SSH instantly releases instruments;
confirm job state through site tools before starting recovery or another run.

Session and artifact boundaries
-------------------------------

Run handles expire when ``start_session`` switches calibration sessions or the
direct server or gateway exits. An interactive client may resume its
conversation history, but that does not preserve the old MCP session or run
handles. Saved files are retained evidence, not a way to call
``update_platform`` in a fresh session.

``start_session(source="run")`` applies fitted results in the new session, so it
requires the same prior inspection and approval as ``update_platform``. Other
starting points select saved or committed software state; they do not undo
physical hardware effects or change the configured instruments and drivers.
The tool preserves old reports and uses a fresh folder for future protocols.

The latest *accepted* snapshot is handed to subsequent queued jobs; rejected
fits are not promoted. This is not rollback protection against acquisition-side
effects or partial update failure.

Promoting a saved snapshot into your source platform definitions is a manual,
reviewed operation. See :doc:`artifacts` for snapshot contents and metadata.
