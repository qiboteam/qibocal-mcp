---
name: qibocal-assistant
description: 'Plan and run one-off qibocal acquisitions through the qibocal-assistant MCP server. USE FOR: selecting protocols from the catalog, acquiring one or more experiments on a qibolab platform, fitting existing acquisition data, generating HTML reports, reviewing run outputs, and applying calibration updates only after explicit user approval. DO NOT USE FOR: autonomous multi-phase calibration sessions (use auto-calibration), editing qibocal protocol code, or non-qibocal hardware.'
---

# Qibocal Assistant

Use the `qibocal-assistant` MCP server for user-directed, one-off qibocal
experiments. The server can run several experiments in one runcard, fit the
results, generate the report, and apply a completed run's platform updates.

The **user is the workflow controller** for this skill. Follow the user's
instruction, whether it is explicit ("fit this folder") or implicit ("show me
the results from this run"), and infer which kind of operation it requests:

- **Acquisition** — run new experiments on a platform.
- **Offline fitting** — fit or refit data that already exists.
- **Offline reporting** — generate a report from an existing output folder.
- **Offline platform update** — apply the fitted update from an existing output
  folder, provided the user has explicitly authorized that update.

Do not start an acquisition merely because this skill supports acquisition. If
the request refers to existing data or an existing output folder, perform only
the requested offline operation or operations. Conversely, do not replace a
request for new measurements with offline analysis.

This workflow is approval-gated: acquisition and analysis may proceed from the
user's request, but platform changes must not be applied until the user has
reviewed the result and explicitly approved the update.

## Interpret the user's instruction

Before calling a tool, determine the requested operation from the user's words
and available context:

| User intent | Typical explicit or implicit instruction | Tool |
| --- | --- | --- |
| Acquire new data | "Run Rabi", "measure qubit 0", "calibrate the readout amplitude" | `read_protocol_catalog`, then `acquire_experiments` |
| Fit existing data | "Fit this run", "recompute the fit", or a request for fitted values from an unfitted folder | `generate_fit` |
| Generate a report | "Generate the report", "show me the results", or a request to inspect a run whose report is missing | `generate_report` |
| Apply an update | "Apply/update the platform using this run" | `update_platform` |

An instruction may imply multiple ordered offline operations. For example,
"fit this folder and show me the results" means `generate_fit` followed by
`generate_report`; it does not mean acquiring new data. Use the minimum set of
tools that fulfills the instruction. If the intent or required folder is
genuinely ambiguous and cannot be inferred from context, ask the user rather
than choosing an acquisition or modifying a platform speculatively.

## Tools

| Tool | Purpose |
| --- | --- |
| `read_protocol_catalog()` | Return the supported operations, parameter names and types, defaults, and platform fields updated by each protocol. |
| `read_platform_configuration(platform_name)` | Return the platform's `parameters.json` and `calibration.json` contents, so the agent can inspect the current platform definition and calibrated values. |
| `acquire_experiments(experiments, parent_folder?, platform?, targets?, backend?, update?, partition?)` | Run, fit, and report one or more experiments in a newly created output folder. |
| `generate_fit(data_folder, output_folder?, update?, force?)` | Fit or refit data in an existing qibocal output folder. |
| `generate_report(data_folder)` | Generate `index.html` and protocol reports for an existing output folder. |
| `update_platform(data_folder, skip_qubits?)` | Apply the fitted updates from a completed output folder. `skip_qubits` excludes specific qubits from the update and may be passed only when the user explicitly asks to skip those qubits. Call only after explicit user approval. |

## Acquisition workflow

Use this workflow only when the user's instruction asks for new measurements,
either explicitly or by clearly naming an experiment to run.

1. Read the protocol catalog before planning an acquisition.
2. Map the user's goal to the catalog operation or operations.
3. Build each experiment with:
   - `operation`: the exact registered operation name.
   - `parameters`: every protocol parameter, including optional parameters,
     using catalog names, types, and documented defaults.
   - `targets`: only when that experiment needs targets different from the
     top-level target list.
   - `update: true`.
   - `id`: a unique, descriptive identifier when running multiple experiments.
4. Call `acquire_experiments` with:
   - the complete `experiments` list,
   - the requested `platform`,
   - the requested top-level `targets`,
   - `parent_folder` when the user specifies an output location,
   - `partition` when the user requests Slurm execution,
   - `backend` only when the user requests a non-default backend,
   - **`update: true`**, always.
5. Inspect the returned `output_folder` and report artifacts. Summarize whether
   the acquisition and fit produced a physically credible result. Do not treat
   a serialized fit value alone as proof of a good calibration.
6. Ask the user whether to apply the fitted platform updates.
7. Call `update_platform` with the exact returned `output_folder` only after an
   explicit approval. If approval is declined or absent, leave the platform
   unchanged.

## Planning rules

- The catalog is the source of truth. Never invent operation names, parameter
  names, types, defaults, or update effects.
- **Golden rule for `read_platform_configuration`:** call it only when planning
  an acquisition (i.e., before `acquire_experiments`) and only when the agent
  must infer parameter values — either because the user explicitly asked to
  base the experiment on the current platform state, or because the user left
  the freedom to choose the experiment parameters itself. Never call it for
  offline operations (`generate_fit`, `generate_report`, `update_platform`);
  those operate on an existing output folder and do not need the platform
  definition.
- Set all catalog parameters, not only required parameters. If a required value
  cannot be inferred safely from the request or platform context, ask the user
  for it before acquisition.
- Preserve numeric range shapes exactly as documented, such as
  `(start, end, step)`.
- Use top-level `targets` when all experiments share the same targets. Use an
  experiment-level `targets` field only to override that list for one action.
- Give every action a distinct `id` when a runcard contains multiple
  experiments.
- Order dependent experiments so earlier results provide the platform
  quantities required by later experiments.
- Use one `acquire_experiments` call when the requested experiments form one
  coherent runcard. Split them only when the later acquisition must be replanned
  after reviewing an earlier result.
- Omit `partition` for local execution. When present, it selects the Slurm
  partition used by the server.
- Do not infer `skip_qubits` from fit quality, report inspection, failed
  experiments, or prior context. Use it only when the user explicitly names the
  qubits whose updates must be skipped.
- Report tool or subprocess failures directly. Do not claim that acquisition,
  fitting, reporting, or updating succeeded when a tool failed.

## Update safety

`acquire_experiments` defaults to applying updates, so never rely on its
default. Pass `update: false` at both levels:

```json
{
  "experiments": [
    {
      "id": "example",
      "operation": "catalog_operation",
      "parameters": {},
      "update": false
    }
  ],
  "update": false
}
```

Even if the initial request asks for calibration, first acquire with updates
disabled, review the report, and obtain confirmation for the concrete output
folder. Apply it only through `update_platform`.

## Existing-data workflow

Use the standalone offline tools when the user's instruction concerns an
existing qibocal output folder. Do not acquire anything unless the user also
asks for new data:

1. Call `generate_fit(data_folder, ...)` only when the user asks for fitting or
   fitting is necessarily implied by the requested offline result.
   - Keep `update: false` unless the user has separately approved applying the
     result.
   - Set `force: true` only when the user requests overwriting or recomputing an
     existing fit.
   - Use `output_folder` only when the fit should be written somewhere other
     than `data_folder`.
2. Call `generate_report(data_folder)` only when the user asks for a report or
   inspection requires generating a missing report.
3. Review and summarize results when requested or when needed to support an
   update decision.
4. Call `update_platform(data_folder, skip_qubits?)` only when the user's
   instruction explicitly authorizes applying that specific run. A request to
   fit, report, inspect, or "check" a run is not update approval.
   - Omit `skip_qubits` to apply every update contained in the run.
   - Pass `skip_qubits` only when the user explicitly asks to exclude specific
     qubits, using exactly the qubit identifiers the user named.
   - Never decide independently to skip a qubit, even when its fit appears poor.
     Explain the concern and let the user decide whether to request a selective
     update.

## Boundary with auto-calibration

Use this skill for bounded acquisitions chosen directly from a user request.
For a stateful, autonomous calibration that progresses through multiple phases,
reviews each step, selectively accepts qubits, and maintains a session-local
working platform, use the `auto-calibration` skill and the
`qibocal-calibrator` server instead.
