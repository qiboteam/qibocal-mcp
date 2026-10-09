Artifacts and report inspection
===============================

Where files live
----------------

Direct execution writes ``OUTPUT/<run_id>/``; with default settings this is
``runs/<run_id>/``. Queued execution isolates each gateway session under
``OUTPUT/sessions/<session_id>/<run_id>/``.

With SSH, these paths are on the cluster, not on the laptop. Results and images
return over MCP, but copying the remaining files is a separate operation.
Use the returned ``folder`` or final agent summary rather than guessing IDs.

A run directory contains, depending on protocol support and execution success:

.. code-block:: text

   <run_id>/
     run.json
     ... protocol data files ...
     ... fitted results files, if present ...
     report-0-0.html
     report-0-0.png
     ... more target/figure report files, if present ...
     platform/                 # after a successful accepted update
       parameters.json
       calibration.json

Data and fitted-result filenames are owned by the pinned Qibocal protocol and
serializer; they are not a single universal schema. Report indices enumerate
targets and figures, not necessarily the target's literal qubit ID.
HTML reports are interactive Plotly output. PNGs are exported at
1200 by 700 pixels using Kaleido/Chrome.

Run metadata
------------

``run.json`` and the structured execution response include:

* ``run_id``, ``platform`` and ``slurm_job_id`` (possibly null).
* ``protocol``, submitted ``parameters`` and selected ``targets``.
* ``folder`` on the execution host.
* Serialized fitted ``results``, or null when absent.
* ``supports_update``, ``updated`` and ``update_started``.

These are records of the run, not an automatic fit-quality verdict.
The initial execution response reports update flags as false; a later update
modifies the saved metadata rather than rewriting the previous MCP response.
An attempted update may leave ``update_started`` true and ``updated`` false.
See :doc:`safety` before recovering from this situation.

MCP images and evidence
-----------------------

The server returns report tables as text and PNG reports as **MCP image
content**, not merely paths. A vision-capable client can inspect the images
without mounting cluster storage. A protocol without reports or fitted
results may return fewer content blocks.

.. figure:: ../_images/illustrative-report.svg
   :alt: Synthetic spectroscopy illustration: sample markers are compared with a blue fit across a frequency-offset sweep. The central feature has baseline on both sides, with notes to inspect residual agreement and consult actual fit uncertainty. This picture is illustrative and contains no hardware measurements.
   :width: 100%

   Original synthetic picture for learning report interpretation, not a
   genuine acquisition or a Qibocal-generated report.

Inspect actual signal quality, model agreement, uncertainty and sweep coverage.
The :doc:`../tutorials/local-calibration` tutorial explains these report features
with a companion synthetic PNG picture.
There is no fixed acceptance threshold supplied by the bridge.

Snapshots and retention
-----------------------

An accepted update saves both Qibolab parameters and Qibocal calibration under
the run's ``platform/`` directory. It changes the live direct-mode platform
or becomes the accepted state for subsequent queued jobs. It does not
overwrite source files in ``QIBOLAB_PLATFORMS``.

Keep the paired snapshot files together. Promotion into your authoritative
platform definitions is a separate manual operation requiring review.
Files persist after a session exits, but run handles do not; persistence is not
full agent-session resume support.

Treat reports, platform definitions and calibration output as potentially
sensitive experiment data. Share or copy them only under your site's policy.
