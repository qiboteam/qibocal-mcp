Safety, limits and recovery
===========================

Trusted-agent draft
-------------------

This is a single-platform draft for a trusted agent, not a production hardware
safety layer. Natural-language limits and budgets guide the model but are not
enforced instrument interlocks or a deterministic workflow engine.
Use site-approved hardware protections, exclusive access and operator oversight.

Put explicit amplitude/frequency limits, shot limits, an execution budget and
stopping criteria in every objective. Choose a vision-capable model and require
inspection of the actual tables and images. There is no built-in fit acceptance
threshold. Never treat synthetic/mock data as evidence of a successful
hardware calibration.

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

Queue/subprocess failures are surfaced to the model as errors. Cancellation of
an active gateway operation terminates its local ``srun`` controller; cleanup
waits up to ten seconds before killing a controller that has not exited.
SLURM/site signal handling governs how quickly the allocation and remote work
actually stop. Do not assume that closing SSH instantly releases instruments;
confirm job state through site tools before starting recovery or another run.

Session and artifact boundaries
-------------------------------

Run handles expire when the direct server or gateway exits. Resuming a complete
agent session is unsupported. Saved files are retained evidence, not a way to
call ``update_platform`` in a fresh session.

The latest *accepted* snapshot is handed to subsequent queued jobs; rejected
fits are not promoted. This is not rollback protection against acquisition-side
effects or partial update failure.

Promoting a saved snapshot into your source platform definitions is a manual,
reviewed operation. See :doc:`artifacts` for snapshot contents and metadata.
