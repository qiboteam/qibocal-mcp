Artifacts and report inspection
===============================

Where files live
----------------

Both direct and queued execution collect one standard Qibocal report per MCP
session under ``OUTPUT/sessions/<session_id>/``. With default settings this is
``runs/sessions/<session_id>/``. Each execution has a dedicated
``data/<protocol>-<iteration>/`` directory; repeated protocols get new iterations
rather than overwriting earlier runs. Discovery alone does not create a report.

With SSH, these paths are on the cluster, not on the laptop. Results and images
return over MCP, but copying the remaining files is a separate operation.
Use the returned ``folder`` (protocol artifacts) and ``report_folder`` (whole
session report), or final agent summary, rather than guessing IDs.

A session report contains, depending on protocol support and execution success:

.. code-block:: text

   <session_id>/
     meta.json
     history.json
     platform/                 # initial session platform, never overwritten
     new_platform/             # current session platform
     data/
       <protocol>-0/
         action.yml
         run.json
         ... protocol data files ...
         ... fitted results files, if present ...
         report-0-0.html
         report-0-0.png
         ... more target/figure report files, if present ...
         platform/             # immutable snapshot after an accepted update
           parameters.json
           calibration.json
       <protocol>-1/
         ...

``meta.json`` records Qibocal/backend versions, the start time, the latest
successful operation's end time, accumulated targets and per-execution acquisition
and fit timings. ``history.json`` retains execution order across protocols and
iterations, including across fresh SLURM workers. Each ``action.yml`` records
the protocol, targets and resolved parameters, including platform defaults;
its update flag becomes true only after an accepted update.
``new_platform/`` initially matches ``platform/`` and is refreshed as successful
executions and accepted updates change the live session state.
Serve ``OUTPUT`` with ``qibocal-report-server`` to discover these session reports.

Data and fitted-result filenames are owned by the pinned Qibocal protocol and
serializer; they are not a single universal schema. Report indices enumerate
targets and figures, not necessarily the target's literal qubit ID.
The bridge consumes ``Protocol.report`` output through ``Executor.report``:
Plotly figures and HTML tables, not a full Qibocal HTML report-page template.
Saved HTML files are individual interactive Plotly figures. PNGs are exported at
1200 by 700 pixels using Kaleido/Chrome.
With native Copilot's ``--report-rendering client``, the execution host saves
``report-I-J.json`` instead of PNGs alongside the HTML reports. Plotly figures
travel over MCP and PNGs are rendered and retained only in the client cache.
This avoids requiring Chrome on compute nodes.

Run metadata
------------

``run.json`` and the structured execution response include:

* ``run_id``, ``platform`` and ``slurm_job_id`` (possibly null).
* ``session_id`` and ``report_folder`` for the shared Qibocal report.
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

Pydantic AI consumes those inline images unchanged. Native Copilot instead
uses a proxy to stage returned PNG bytes on the client host for its ``view``
tool, because tested Copilot adapters drop inline MCP images. Native responses
add ``copilot_report_images`` and ``inspection_required`` to text and structured
metadata without replacing original content. These local paths are separate
from the execution host's ``folder`` and do not provide general access to
remote files.
In client-rendering mode, the proxy replaces embedded Plotly resources with
locally rendered MCP images before adding the same inspection metadata.

The native cache is
``$XDG_CACHE_HOME/qibocal/agent/copilot-reports/<session>/<call>/report-N.png``,
falling back to ``~/.cache/qibocal/agent/copilot-reports`` when ``XDG_CACHE_HOME``
is unset or empty. PNG-containing folders are private (``0700``); staged files
persist with no automatic cleanup. Remove selected session directories after
inspection according to site retention policy. An image-capable Copilot model
must actually inspect the PNGs; staging alone is not evidence.
See :doc:`../agent/copilot` for viewer permissions and model limitations.

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
the run's ``platform/`` directory and refreshes the session's ``new_platform/``.
The immutable run snapshot, rather than the mutable session snapshot, is used
for queued accepted-state handoff, so a failed later update cannot overwrite
the previously accepted snapshot. An update changes the live direct-mode platform
or becomes the accepted state for subsequent queued jobs. It does not
overwrite source files in ``QIBOLAB_PLATFORMS``.

Keep the paired snapshot files together. Promotion into your authoritative
platform definitions is a separate manual operation requiring review.
Files persist after a session exits, but run handles do not. Resuming a Copilot
conversation or reusing an exported plugin cannot restore terminated
server/gateway execution state.

Treat reports, platform definitions and calibration output as potentially
sensitive experiment data. Share or copy them only under your site's policy.
