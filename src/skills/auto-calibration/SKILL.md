---
name: auto-calibration
description: 'Drive an autonomous qibocal calibration through the auto-calibration MCP server (qibocal-calibrator). USE FOR: running a full from-scratch or incremental calibration session on a qibolab platform, planning the protocol sequence per phase (resonator, qubit, signal, single-qubit gates, readout, RB), running one protocol at a time with run_protocol, reviewing fits in the HTML report, accepting or discarding per-qubit updates with accept_step_platform, handling local-oscillator placement, and closing the session with finish_calibration or abort_calibration. DO NOT USE FOR: one-off single-protocol acquisitions without a session (use the qibocal-assistant acquire_experiments tool instead), editing protocol code, or non-qibocal hardware.'
---

# Auto Calibration (qibocal-calibrator MCP server)

The auto-calibration MCP server (`qibocal-mcp calibrator`) runs a **stateful,
step-by-step calibration session**. Each `run_protocol` call is its own
subprocess writing into a fresh step folder under the session `data_folder`;
no hardware connection is kept between calls. The working platform lives in a
session-local folder, **never** in the global Qibolab registry.

This skill is a **fully autonomous** calibration run. After the initial user
request, do **not** ask for approval or additional instructions at every step.
Start once with `start_calibration`, then proceed without awaiting further user
input until the calibration goal is reached and the session is closed with
`finish_calibration`, or the calibration cannot be completed and the session is
closed with `abort_calibration`.

## Tools

| Tool | Purpose |
|------|---------|
| `start_calibration(parent_folder, targets, platform, partition?)` | Start one session. `partition` selects the Slurm partition for every subsequent `run_protocol` (via `sbatch --wait --time=01:00:00`); omit to run locally. Returns the session `data_folder` and the session-local `working_platform` path. |
| `get_platform_architecture(platform_name)` | Parse the platform `parameters.json`: qubit names, connectivity topology, flux-tunable qubits, couplers. Call BEFORE planning the strategy. |
| `read_platform_files()` | Read the session's last saved platform `calibration.json` and `parameters.json` (the session-local platform updated by `accept_step_platform`). Returns the parsed `calibration` and `parameters` together with the session `platform` name and `platform_path`. Use this whenever you need to consult the session platform files. |
| `read_protocol_catalog(filename)` | Read a protocol catalog markdown file (see the Resource-reading tools section). |
| `read_calibration_report(report_path)` | Read a run's HTML report (fit plots + serialized results) for visual review. |
| `run_protocol(operation, parameters, targets?)` | Run one protocol in parallel across the selected targets. `targets` must be a non-empty subset of the session targets; omit to use all. Always set ALL protocol parameters, including optional ones. |
| `accept_step_platform(accepted_qubits)` | Promote updates for the listed qubits from the latest step to the session platform; updates for all other qubits in that step are discarded. Pass an empty list to keep the previous platform. |
| `manually_overwrite_qubits(manual_overwritten_qubits, operation)` | Force the next `run_protocol` to repeat `operation` for exactly the given qubits with focused parameters (used after a manual platform estimate). |
| `finish_calibration()` | Close the session, accepting the latest step platform, and return a summary. |
| `abort_calibration()` | Close the session without accepting the latest step; step data is preserved. |

## Resource-reading tools

- `read_protocol_catalog(filename)` — the protocol catalog markdown files.
  Read the file for the phase you are about to run:
  - `Resonator_Characterization.md` (Phase 1)
  - `Qubit_Characterization.md` (Phase 2)
  - `Signal_Experiments.md` (Phase 3)
  - `SingleQubit_Experiments.md` (Phase 4)
  - `Readout_Experiments.md` (Phase 5)
  - `SingleQubit_RB.md` (Phase 6)
  - `PROTOCOL_CATALOG.md` (full reference of every protocol, its parameters,
    and the "Platform update fields" table used for manual estimates)
- `read_calibration_report(report_path)` — the run's HTML report
  (fit plots + serialized results) for visual review. Pass the step's
  `output_folder` as the `report_path` argument.

## Core principles

- **Diamond rule — activate the virtual environment first.** Before **every**
  tool call (including `start_calibration`, `run_protocol`,
  `accept_step_platform`, `finish_calibration`, etc.), the agent **must**
  activate the project virtual environment by sourcing its absolute path:
  `source /home/users/lorenzo.ballerio/qibocal-mcp-servers/.venv/bin/activate`.
  This ensures all subprocesses run with the correct Python interpreter and
  dependencies. Never call any tool without first activating the venv.
- **Platform-files golden rule — consult the session platform via
  `read_platform_files()`.** Everytime you need to consult the session platform
  files (`parameters.json` and/or `calibration.json`) — to pick sweep ranges, to
  apply the Local Oscillator rules, or to make a manual platform estimate — call
  `read_platform_files()` to read the session-local platform (the one updated by
  `accept_step_platform`) instead of reading the files directly from disk. It
  returns the parsed `calibration` and `parameters` together with the session
  `platform` name and `platform_path`.
- **Autonomy.** Do not ask for approval at every step; run autonomously between
  `start_calibration` and `finish_calibration`/`abort_calibration`.
- **Parallel by design.** All protocol executions are parallel: one runcard and
  one Slurm job for the selected targets, never a sequential loop.
- **One protocol at a time.** Call `run_protocol` for a single protocol, review
  the fit, then decide the next step.
- **Calibration-graph golden rule — use evidence-driven feedback loops.** The
  phase sequence is the default progression, not a strictly linear path. While
  working within a phase, evaluate whether repeating a different or previously
  run protocol in that same phase is necessary to improve or validate the
  calibration. If results show that an earlier dependency needs correction,
  return to the relevant previous phase, run only the necessary experiments
  there, then resume forward progression. Do not run experiments from later
  phases ahead of the current phase, and do not revisit earlier phases without
  evidence that doing so is needed.
- **Set ALL parameters.** For every protocol, set every parameter, including the
  optional ones, using the catalog as the source of truth for names, types, and
  defaults.
- **`targets` is a non-empty subset** of the session targets passed to
  `start_calibration`; using the entire session target set is also valid. Omit
  `targets` to use the entire set.
- **`update=true`** only when that protocol's successful fit should be applied to
  the platform for subsequent steps. The platform is NOT published to the Qibolab
  registry during a step.
- **Fail closed when judging fits.** Treat every fit as failed by default. Accept
  it only after affirmative evidence shows that the data and fit are very good:
  first confirm the experiment's resolution is sufficient to resolve the signal
  by inspecting sweep spacing and the number of measured points across the
  feature. If needed, calculate spacing from the sweep range and number of points
  for a uniform sweep, or use adjacent axis values for a nonuniform sweep, and
  compare it with the feature width. Then verify that the raw signal is clear,
  the fitted model tracks it, residuals show no meaningful structure, the fit
  converged reliably, and the result is physically plausible. If any check is
  inconclusive or lacks supporting evidence, immediately discard that result;
  do not hesitate or advance as if it were calibrated. Repeat or refine the same
  experiment, choose another applicable protocol in the same phase, or, when an
  earlier dependency may be responsible, step back to the relevant previous
  phase under the calibration-graph golden rule. A successful optimizer status
  or serialized result alone is never sufficient evidence of fit quality.
- **Accept before refining.** If at least one qubit is accepted, you **must**
  call `accept_step_platform` to accept the current step before proceeding with
  further refinements.
- **Iterate, don't be greedy.** Performing multiple iterations of the same
  protocol, even with different qubits and parameter ranges, is often necessary
  for a good-quality calibration. Allow at most 3-4 refinements per protocol;
  abort if trustworthy results remain unavailable after that.

---

## Step-by-step calibration

### Step 0 — Start the session

Call `start_calibration` **once** with:
- `parent_folder`: the parent directory where the session `data_folder` is
  created (a timestamped subfolder is created automatically).
- `targets`: the list of qubits to calibrate (the session target set).
- `platform`: the Qibolab platform name.
- `partition` (optional): the Slurm partition for every subsequent
  `run_protocol`; omit to run locally.

Record the returned `data_folder` and `working_platform` (the session-local
platform folder). All later platform edits happen on `working_platform`, never on
the global Qibolab platform.

### Step 1 — Understand the QPU architecture

Call `get_platform_architecture` **before** thinking about the strategy. It parses
the platform's `parameters.json` and reports:
- the qubit names and count,
- the connectivity topology (which qubits are connected, from the two-qubit
  native gates),
- which qubits are flux tunable (they have a flux channel in `configs`),
- which couplers are present (flux channels named `coupler_*`).

Understanding the type of transmons (flux tunable or fixed frequency) and the
presence of couplers is **crucial** for selecting the appropriate calibration
protocols for qubits, resonators, and gates.

### Architecture-dependent protocols are mandatory

Use `get_platform_architecture` and the session platform configuration to identify
the hardware features, operating modes, and relationships present in the QPU.
Before choosing the phase sequence, map each relevant feature to its supported
protocols and the targets, channels, or connected components those protocols
require. Treat these architecture-applicable protocols as requirements, not
optional suggestions; the examples below are illustrative, not an exhaustive
list.

- For example, on a flux-tunable architecture, a from-scratch calibration must
  include both `resonator_flux` and `qubit_flux` for each applicable target.
- Likewise, if the architecture includes couplers or connected qubit pairs,
  identify and run the supported, goal-relevant protocols for those components.
- Apply the same reasoning to every other relevant architecture feature. Use
  `read_protocol_catalog("PROTOCOL_CATALOG.md")` and the phase-specific
  catalogs to find supported protocols; do not infer protocol names or omit a
  calibration merely because it is not listed among a phase's typical examples.

In **from-scratch** mode, run all architecture-applicable protocols in the
prescribed phase order even if existing platform values look calibrated. In
**incremental** mode, interpret the user's stated starting point as follows:

- If the user names a **protocol**, its phase is incomplete. Repeat that whole
  phase, including the named protocol and any other architecture-applicable
  protocols needed to complete the phase. For example, if qubit spectroscopy
  was done but the flux sweetspot was not measured, repeat qubit
  characterization and run the applicable flux protocol(s).
- If the user names a **phase**, treat that phase as the completed checkpoint
  and begin with the following phase. For example, if frequencies and
  sweetspots are already calibrated, begin with signal experiments after qubit
  characterization.

Do not infer a different starting point. The selected starting point controls
the planned progression; evidence from results may still justify revisiting an
earlier phase under the calibration-graph golden rule.
After handling the user-selected starting point, check downstream phases for
missing, stale, or unverified calibration data. If any is found, fall back to
the **earliest downstream phase with a gap** and continue forward from there,
running the applicable protocols needed to fill gaps. Preserve and trust
existing data for quantities that are already current and reliable; do not
rerun those protocols just because they are in a downstream phase. Revisit
otherwise complete downstream results only if the user requests it or a new
result invalidates their assumptions. If the catalogs provide no applicable
protocol or required platform information is unavailable, report the limitation
and abort rather than silently treating that architecture-specific step as
complete.

### Step 2 — Choose the calibration mode

- **From-scratch** (user explicitly asks to calibrate "from scratch" / "from the
  beginning"): follow the ordered phase sequence in Steps 3–8. Use the pre-existing
  parameters as a reference but **do not trust them** — they must be verified and
  potentially updated through the calibration process.
- **Incremental** (the user specifies a starting protocol or phase):
  1. Call `get_platform_architecture` to understand the QPU.
  2. If the user names a protocol, repeat its **entire containing phase**:
     run that phase's applicable protocols, including architecture-dependent
     ones that have not yet been calibrated. Do not infer an earlier phase.
  3. If the user names a phase, treat it as the completed checkpoint and start
     at the **following phase**. Do not rerun the named phase or infer an earlier
     starting point.
    4. After applying that starting rule, check downstream phases for missing,
      stale, or unverified quantities. If any are found, fall back to the
      earliest downstream phase with a gap and continue forward, running only
      protocols needed to fill gaps. Treat existing, current downstream
      quantities as good parameters and preserve them; do not rerun them just
      because their phase is reached. Revisit them only if requested or if a new
      result invalidates their assumptions.
    5. Treat calibration data from phases before the selected starting point as
      trusted and leave those phases unchanged unless results in the active
      session show that an earlier calibration assumption must be corrected.
    6. If the user has not identified a starting protocol or phase, ask for the
     desired starting point rather than inferring one from the platform state.

In both modes, adapt the strategy dynamically: if a fit is poor, revise
parameters or use an alternative protocol to improve it. In the active
calibration scope, do not mark an attempted step complete until its fit is
trustworthy. In incremental mode, previously calibrated downstream phases are
trusted baseline data; revisit them only when requested or when new results
invalidate their assumptions. Apply the calibration-graph golden rule when
deciding whether a same-phase repeat or a return to a previous phase is needed.


**For the following phases you must follow this GOLDEN RULE**:
For each protocol:
1. Call `run_protocol` with the full parameter set and the relevant `targets`.
2. Open the step's report via `read_calibration_report({output_folder})`
   and judge per qubit whether the fitted curve matches the measured signal and
   physical expectation.
3. Evaluate each qubit independently and fail closed: the default is to reject
   its fit. Accept it only when the report provides clear, affirmative evidence
   that the feature is resolved and the fit is very good. If there is any
   uncertainty, missing evidence, or unreliable convergence, discard the result
   immediately; do not hesitate or mark it complete.
4. Call `accept_step_platform` with the accepted qubits (pass an empty list if
  none are trustworthy). If some targets were rejected, run a focused refinement
  for those targets, changing range or resolution, trying another suitable
  protocol in the same phase, or stepping back to a relevant previous phase if
  an earlier dependency needs correction. Continue until their fits are
  trustworthy or the calibration must be aborted. Do not leave rejected targets
  behind or mark their phase complete without a trustworthy result.
5. Before a manual platform estimate for rejected qubits, accept the current
  step for any accepted qubits. Then follow the manual-estimate procedure below.

### Step 3 — Phase 1: Resonator characterization

Read `read_protocol_catalog("Resonator_Characterization.md")` for detailed
instructions and protocol parameters.

Goal: find the resonator (readout) frequency and the readout amplitude for each
target qubit.

Typical protocols (see the markdown for the exact list and parameters):
- `resonator_spectroscopy` — coarse-to-fine sweep of the readout frequency to
  locate the resonator peak. Prefer a 2D protocol when possible.
- `resonator_punchout` / `resonator_punchout_attenuation` — 2D sweep (frequency
  × amplitude or attenuation) to resolve the resonator peak and set the readout
  amplitude. Follow the phase catalog's power-sweep and shared-LO checks.
- `resonator_flux` — for flux-tunable qubits, map the resonator frequency vs
  flux bias to find the sweetspot and set the flux offset.

**Remember:** always follow the golden rule for each protocol in every phase.

### Step 4 — Phase 2: Qubit characterization

Read `read_protocol_catalog("Qubit_Characterization.md")`.

Goal: find the qubit (drive) frequency and, for flux-tunable qubits, the flux
sweetspot.

Typical protocols:
- `qubit_spectroscopy` — sweep the drive frequency to locate the qubit
  transition. Use a 2D protocol when possible.
- `qubit_power_spectroscopy` — 2D sweep (frequency × drive amplitude) to
  identify transitions; follow the phase catalog's drive-power guidance.
- `qubit_flux` — for flux-tunable qubits, map the qubit frequency vs flux bias
  to find the sweetspot and set the flux offset.
- `qubit_spectroscopy_ef` — for qutrit work, find the 1→2 transition and the
  anharmonicity.

**After accepting every spectroscopy protocol (both 1D and 2D), apply the Local
Oscillator rules (see below)** before moving to the next phase.

**Remember:** always follow the golden rule for each protocol in every phase.

### Step 5 — Phase 3: Signal experiments

Read `read_protocol_catalog("Signal_Experiments.md")`.

Goal: calibrate the drive pulse (Rabi) and define state classifiers.

Typical protocols:
- `rabi_amplitude_signal` / `rabi_length_signal` — find the drive amplitude or
  duration that produces a π/2 (RX90) or π (RX) rotation.
- `rabi_amplitude_frequency_signal` / `rabi_length_frequency_signal` — 2D
  variants that also refine the drive frequency.
- `ramsey_signal` — measure the Ramsey fringes to refine the drive frequency and
  estimate T2.
- `t1_signal` / `t2_signal` / `spin_echo_signal` — coherence measurements.
- `single_shot_classification` — fit the readout classifier (IQ angle,
  threshold, ground/excited state means, fidelity).

**Remember:** always follow the golden rule for each protocol in every phase.

### Step 6 — Phase 4: Single-qubit gate optimization

Read `read_protocol_catalog("SingleQubit_Experiments.md")`.

Goal: optimize single-qubit gate fidelity.

Typical protocols:
- `rabi_amplitude` / `rabi_length` — refine the RX/RX90 amplitude and duration.
- `ramsey` — refine the drive frequency and T2.
- `drag_simple` / `drag_tuning` — tune the DRAG β parameter to suppress
  leakage.
- `flipping` — correct the delta amplitude in the drive pulse.
- `allxy` — a simple test of the single-qubit gate calibration (21 gate pairs).
- `t1` / `t2` / `spin_echo` — coherence measurements.

**Remember:** always follow the golden rule for each protocol in every phase.

### Step 7 — Phase 5: Readout optimization

Read `read_protocol_catalog("Readout_Experiments.md")`.

Goal: optimize the assignment (readout) fidelity.
**If not explicitly stated, the default goal for assignment fidelity is at least a 88% for each qubit:**

Typical protocols:
- `ro_amplitude` — optimize the readout amplitude.
- `ro_frequency` — optimize the readout frequency.
- `ro_amplitude_frequency` — 2D optimization of both readout amplitude and
  frequency.
- `readout_characterization` — measure readout fidelity and effective
  temperature.
- `readout_mitigation_matrix` — build the readout error-mitigation matrix.

**Iterate within Phase 5:** if fidelity is below target, go back to Phase 3 or
4 to refine the drive/readout calibration, then re-run the classification
protocols.

**Remember:** always follow the golden rule for each protocol in every phase.

### Step 8 — Phase 6: Single-qubit randomized benchmarking

Read `read_protocol_catalog("SingleQubit_RB.md")`.

Goal: measure the single-qubit gate fidelity via randomized benchmarking.
**If not explicitly stated, the default goal for single-qubit gate fidelity is at least a 99% for each qubit:**

Typical protocol:
- `standard_rb` — run RB sequences over a range of depths and fit the
  exponential decay to extract the per-gate error.

**Remember:** if the single-qubit gate fidelity is below the target, revisit the relevant calibration phases to improve it before proceeding.

### Step 9 — Finalize the session

- When the calibration goal is complete, call `finish_calibration` to finalize
  the session (it accepts the latest step platform and returns a summary).
- If repeated refinements cannot produce trustworthy fits, required protocols or
  platform information are unavailable, or continuing would produce an
  unreliable calibration, call `abort_calibration` instead. Aborting preserves
  the collected step data but does not accept the latest step or publish the
  session platform. Report the reason for aborting and do **not** call
  `finish_calibration` or `update_platform` afterward.

---

## Local Oscillator (LO) rules

Up-converted channels are driven by a local oscillator (LO) mixed with an
intermediate frequency (IF) generated by the controller: the output frequency is
`channel frequency = LO frequency + IF`. In the platform's `parameters.json`,
LOs are the `configs` entries with `"kind": "oscillator"`, named for example
`<qubit>/drive_lo` (qubit drive lines) or `<qubits>/probe_lo` (readout lines);
the channel frequency itself is the `frequency` of the corresponding `iq` entry
(e.g. `<qubit>/drive`, `<qubit>/probe`). The IF is never stored: it is always
derived as `IF = channel frequency - LO frequency`.

Rules to follow, in particular for the qubit drive LOs (`<qubit>/drive_lo`):

- **After accepting every spectroscopy protocol (both 1D and 2D)**, read the
  session platform's `parameters.json` (via `read_platform_files()`) and compute
  `|IF| = |<qubit>/drive frequency - <qubit>/drive_lo frequency|`, also
  considering the full sweep range of the protocol about to be run.
- If the LO is too close to the qubit frequency (roughly `|IF| < 10 MHz`, or the
  sweep would cross or approach the LO frequency), the LO leakage and its image
  sideband overlap the qubit transition and corrupt the measurement. In that case
  move the LO away from the qubit frequency by about 50–70 MHz, usually
  **decreasing** the LO frequency, so that the qubit sits 50–70 MHz above the LO
  (`LO ≈ qubit frequency - 50...70 MHz`).
- Keep `|IF|` within the controller bandwidth (never push it to hundreds of
  MHz); when sweeping, make sure the whole sweep stays on the same side of the
  LO and never crosses it.
- **Only edit the LO entry:** change the `frequency` of the `oscillator` config
  in the session platform's `parameters.json` (the `working_platform` returned by
  `start_calibration`), never the global Qibolab platform. Keep the value type
  identical to the original (e.g. integer Hz stays integer) and do not touch the
  qubit's `drive` frequency, which remains the physical transition frequency.
- LOs can be shared between qubits (the name then lists several qubits, e.g.
  `01/probe_lo`, or several channels reference the same LO). Before moving a
  shared LO, check the resulting `|IF|` of every channel attached to it and pick
  a value that satisfies the rules above for all of them.
- The same reasoning applies to readout (`probe_lo`) and `drive12` channels: keep
  the resonator/transition frequency at a safe distance from its LO.
- After moving an LO, re-run the affected drive protocol to confirm the signal;
  previously acquired data taken with a bad LO placement should not be trusted.

## Parameter selection: quick-and-dirty first

When choosing protocol parameters (frequency ranges, sweep steps, amplitude
ranges, pulse durations, etc.), do not overcomplicate the initial run; start
with a reasonable guess and refine as needed. The sweep range, though, must not
be excessive.

After each run, open the step report using
`read_calibration_report({output_folder})` and judge each target
individually. **Assume the fit failed unless the report proves otherwise. Be very
critical and check these in order:**
1. **Resolution and signal clarity first.** Confirm the sweep samples the feature
  densely enough to resolve its shape and locate it reliably, and that the raw
  signal clearly distinguishes it from noise or background structure. If
  necessary, calculate the spacing for a uniform sweep as `(maximum - minimum)
  / (number of points - 1)`; for a nonuniform sweep, use the actual adjacent
  axis values. Compare the spacing with the observed feature width and check
  how many samples cover it. If the feature is undersampled or unclear, reject
  the result and refine the sweep before assessing or trusting the fit.
2. Verify that the fitted curve tracks the measured signal across the full trace;
  inspect residuals for structure and confirm reliable fit convergence.
3. Check that the fitted result is physically plausible and precise enough for
  the calibration goal.

Accept only when every check has affirmative evidence of a very good fit. A
serialized fit result, an optimizer success flag, or the mere presence of a
plotted curve is not proof. If any check is inconclusive, treat the experiment as
failed and discard it immediately. Do not hesitate to restart: repeat the same
experiment with justified changes to range or resolution, use another suitable
protocol in the same phase, or step back to a previous phase if an earlier
dependency may be responsible. The following manual-estimate procedure is only
for a failed fit when the raw signal still makes the quantity unmistakable; it
does not make the failed fit acceptable and must be followed by a rerun.
- If the **fit is poor** (e.g., the Lorentzian/sine model does not converge, or
  the fitted value is unphysical) but you can **confidently read the quantity
  directly from the raw signal** (e.g., the peak is visually obvious even though
  the automated fit failed), do the following:
  1. If any qubits from this step were accepted, call `accept_step_platform`
     for them **before** making manual edits. This preserves their good updates
     while rejected qubits remain excluded.
  2. Estimate the quantity from the raw signal (peak position, oscillation
     period, etc.) for the rejected qubit.
  3. Look up the **Platform update fields** table for the protocol you just ran
     in `read_protocol_catalog("PROTOCOL_CATALOG.md")`. Do not guess a path
     from scratch: pick the row whose field matches the quantity you estimated.
     Then read the path to modify carefully:
     - If the path starts with `parameters`, edit the platform's
       `parameters.json` using the exact path under that file; infer any
       placeholder entries such as `target` or `qubit` from the actual qubit
       being calibrated.
     - If the path starts with `calibration`, edit the platform's
       `calibration.json` using the exact path under that file; again infer any
       placeholder entries such as `target` or `qubit` from the actual qubit.
     When writing the new value, cast it to the same Python type as the original
     value at that location (for example, preserve `float`, `int`, `bool`, or
     `list` structure instead of writing a string or a different numeric type).
     Also keep the literal path structure that the platform expects instead of
     inventing a new one. Save the edit in the latest step's platform data
     folder (`session_latest_step_folder`), not in the global Qibolab platform.
  4. Call `manually_overwrite_qubits` with `manual_overwritten_qubits` set to
     exactly the affected qubits and `operation` set to the latest protocol.
     Then re-run that **same protocol** for exactly those qubits, with sweep
     parameters re-centered on the estimate (and a suitable range and step), so
     the fit has a chance to converge around the correct value.

Do not jump directly to a high-resolution sweep: first confirm that the feature
exists with a coarse pass. Iterate the refine-and-recheck loop for at most 2–3
refinements per protocol. If the fit is still not trustworthy, do not accept it;
try a justified alternative or abort rather than proceeding with an unreliable
calibration. Whenever at least one qubit is accepted, call
`accept_step_platform` before any further refinement or manual platform edit.

## Completion and abort conditions

- When the requested calibration is complete and all required quantities have
  trustworthy results, call `finish_calibration` to finalize the session.
- Call `abort_calibration` if repeated refinements cannot produce trustworthy
  fits, required protocols or platform information are unavailable, or continuing
  would produce an unreliable calibration. Aborting preserves collected step
  data but does not accept the latest step or publish the session platform.
- Report the reason for aborting. After aborting, do not call
  `finish_calibration` or `update_platform`.
