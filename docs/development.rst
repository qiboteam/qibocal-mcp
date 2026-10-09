Development and documentation builds
====================================

Source layout
-------------

* ``src/qibocal_mcp/calibration.py``: shared calibration policy and context/tool
  identifiers; the generated Copilot profile renders this policy rather than
  maintaining a separate committed copy.
* ``src/qibocal_mcp/deployment.py``: dependency-light ``DeploymentSettings`` and
  ``launch_for`` contract shared by both agent integrations.
* ``src/qibocal_mcp/agent.py``: Pydantic AI transport adapter, reusable
  ``open_calibration_agent`` async context manager and one-shot CLI.
* ``src/qibocal_mcp/copilot.py``: transient native Copilot launch and persistent
  plugin export, independent of the Pydantic AI extra.
* ``src/qibocal_mcp/copilot_server.py``: native-only FastMCP proxy and report-PNG
  staging on the client host, preserving one connected upstream client and its
  tool schemas for the session.
* ``src/qibocal_mcp/models.py``: native Copilot and OpenWebUI model selection.
* ``src/qibocal_mcp/server.py``: responsive direct-server frontend and deployment
  entry point, with background hardware startup.
* ``src/qibocal_mcp/runtime.py``: internal main-thread hardware server, also used
  directly inside SLURM workers.
* ``src/qibocal_mcp/artifacts.py``: incremental task-backed Qibocal report storage
  shared by direct execution and successive SLURM workers.
* ``src/qibocal_mcp/sessions.py``: paired calibration checkpoints and read-only
  repository revision selection for switching calibration sessions.
* ``src/qibocal_mcp/slurm.py``: gateway serialization, dispatch and accepted-state
  handoff.
* ``src/qibocal_mcp/worker.py``: one internal operation per allocation, with
  JSON on stdin/stdout. Workers do not read user configuration.
* ``src/qibocal_mcp/config.py``: validated persistent defaults and management CLI.
* ``src/qibocal_mcp/environment.py``: execution-host environment bootstrap,
  cached source/repositories and dependency provisioning.
* ``tests/``: ``unittest`` coverage for configuration, transports, direct server
  behavior and queued state handoff.

The distribution is named ``qibocal-agent``. The ``qibocal_mcp`` module and
existing MCP commands keep their names. Configuration lives in
``qibocal/agent.json``; caches and generated plugin data use ``qibocal/agent``
under their respective XDG roots.
Installed-source deployment reads ``qibocal-agent`` distribution metadata.
Review :doc:`installation` before changing the pinned Qibocal API.
Preserve stdout for MCP/worker protocols, and keep model API calls out of the
server. Operational invariants are described in :doc:`guides/architecture`
and :doc:`guides/safety`.

Provisioning invariants
-----------------------

Keep shared policy, deployment and source-payload imports independent of the
agent extra and model credentials. The Pydantic AI agent resolves the provider
before deployment/hardware; native Copilot owns its login and model selection.
The SSH bootstrap
uses the standard library to transfer only server source and package metadata
from the launching checkout or installed distribution. The installed-source
fallback must preserve core dependencies and the Qibocal Git pin from distribution
metadata without requiring a local checkout, including for a wheel-installed
agent. Reconstruct minimal installation metadata from ``distribution.requires``.
Retain this installed-wheel path for both agent integrations; sharing deployment
must not make source-payload discovery import the optional Pydantic AI backend.
Keep the pin as a PEP 508 direct Git requirement in ``project.dependencies``,
not only ``tool.uv.sources``, so pip and built distributions retain it.
The launched server receives
resolved settings and ``--no-config``; target-host and worker user configuration
must not be read.
Preserve the Qibocal pin in ``pyproject.toml`` in managed environments and keep
provisioning diagnostics on stderr, never the MCP/worker protocol stream.

SSH agents without an explicit server interpreter default to managed
``qibocal-mcp``; local agents/standalone servers require ``venv`` to manage an
environment. Reject ``venv`` with explicit ``server_python``/``worker_python``
as appropriate. Repository/dependency installation without a managed
environment intentionally modifies the selected interpreter; document that
behavior rather than implying isolation. Standalone gateways with distinct
explicit worker interpreters must provision repository/dependency settings into
both gateway and worker interpreters, each already containing the server;
agents use the same installed interpreter for workers.

Native Copilot profiles allowlist Qibocal, ``view`` and ``ask_user`` without
pinning a model, changing global settings, automatically installing a plugin
or adding blanket permission allowances. ``view`` is a general file/image reader;
instructions restrict its use to staged report PNGs, not enforced image-only
access. Tool restriction also requires the launch command's
``--disable-builtin-mcps --excluded-tools=skill,sql`` flags; selecting a profile
without these flags in an existing session can expose implicit utilities.
Keep wrapper and printed export commands aligned, and reject passthrough
``--available-tools``/``--excluded-tools`` overrides.
Persistent exports contain runtime
environment references, not secret values. Transient plugins are regenerated
per launch; persistent exports snapshot policy and resolved deployment/defaults.
They also record forwarded environment names, excluding model-provider
credential/configuration prefixes and GitHub authentication tokens but preserving
``COPILOT_MCP_*`` driver variables; changing that name set requires re-export.
Missing runtime names must fail clearly, and resolved values must remain literal
rather than undergo
recursive expansion. Direct Copilot launch uses the qualified
``qibocal-calibration:qibocal-calibration`` plugin/profile ID.
The native timeout adapter converts seconds to milliseconds and rejects values
outside 0.001 through 2147483.647 seconds, inclusive, to respect Node's integer
timeout limit without changing the shared 3600-second default.
Neither conversation history nor a stable exported plugin restores a terminated
server/gateway's run handles. Keep model APIs outside the MCP server and do not
describe instructions or tool restrictions as hardware safety enforcement.

Native image staging uses existing FastMCP core dependencies, not Pydantic AI.
``StageReports`` preserves original response content/metadata and adds
``copilot_report_images`` and ``inspection_required`` to text and structured
metadata. PNG bytes arrive over MCP for all deployment modes; no remote file
download or shell tool is needed. Staged files live in private client-local cache
folders and persist until manually removed. Keep Pydantic AI's original inline
image path unchanged, and do not imply that staging enforces inspection,
approval or provider-independent pixel delivery.

Caches belong to the execution host under ``qibocal/agent``, not notebook
``qibocal/envs``. Reuse environments and fingerprint only successful dependency
state; reconcile changed source/dependencies/requirements without silently
continuing on errors or deleting/recreating broken/custom environments.
Include nested local ``-r``/``-c`` files, root packaging metadata and Git ``HEAD``
in reconciliation hashes. Managed environments must run installer dependency
checks and verify server importability before launch.
Installation is additive, not exact sync. Cached repository clones must not
automatically pull/fetch/reset. Validate relative subdirectories against
traversal and symlink escape, and discover requirements only at the documented
root/subdirectory/selected-platform locations, deduplicating files and
delegating ``-r`` includes to pip. Resolve repository and explicit dependencies
together in one resolver request, not with precedence overrides.

Maintain explicit-path/repository/inherited-host platform lookup order and
CLI whole-list replacement for ``platform_repo`` and ``dependency``. Only
``venv`` expands ``~`` on the execution host; existing path settings stay
literal. SLURM assumes identical absolute shared environment, source/clone
cache, working, platform and output paths, with no node-local staging.
See :doc:`guides/configuration` and :doc:`guides/networking` for the complete
user-facing contract and trust/authentication requirements.

Existing tests
--------------

With dependencies installed:

.. code-block:: sh

   uv sync --extra agent
   uv run python -m unittest discover -s tests

For changes limited to one area, use the existing test selector, for example:

.. code-block:: sh

   uv run python -m unittest discover -s tests -p 'test_config.py'

Tests use synthetic callbacks and patched transports/rendering to exercise
behavior without claiming a real hardware calibration or requiring a live
SLURM cluster. Synthetic success is not validation of your instruments.

Build this documentation
------------------------

The site uses native reStructuredText and the Furo theme, following Qibolab's
Qibo branding. The optional ``docs`` extra installs Sphinx and Furo; no Sphinx
extensions are needed.

The light/dark Qibo logos and favicon in ``docs/_static/`` are copied unchanged
from `Qibolab <https://github.com/qiboteam/qibolab/tree/e73aebfac321f99dfa67f19d1567dd84eaf9f980/doc/source>`_.
Their Apache 2.0 license is included as ``docs/_static/LICENSE.qibolab``.
Theme colors and repository links are configured in ``docs/conf.py``.

.. code-block:: sh

   uv sync --extra agent --extra docs
   uv run sphinx-build -W --keep-going -b html docs docs/_build/html

Open ``docs/_build/html/index.html``. ``-W`` treats warnings as failures;
``--keep-going`` reports all warnings before the build exits.
Sphinx reads static documentation without importing project modules, connecting
to hardware or requesting model credentials. Generated output is not source
and should remain untracked.

Basic documentation has two independent tracks: ``agent/index`` comes first,
with Pydantic AI installation, getting started, a reusable Python example,
``agent/copilot`` for native interactive use and tutorials; ``mcp/index`` has
standalone server installation and getting started. Each installation page
puts minimal setup before advanced customization. Shared conceptual guides
and CLI/MCP reference follow both tracks. Add new pages to a toctree so readers
can navigate to them.

Illustrations and accessibility
-------------------------------

Original SVG assets live in ``docs/_images/`` and are embedded using
``figure`` directives with descriptive ``:alt:`` text. SVGs also include titles
and descriptions. Preserve readable labels, contrast and text explanations;
color must not be the only way to distinguish outcomes.

The report picture is explicitly synthetic and illustrative. Never replace
that label with an implication of genuine hardware evidence. Actual reports
and experiments require their own provenance and site-approved data handling.

``synthetic-spectroscopy.png`` was generated with the existing Plotly/Kaleido
dependencies and Chrome, without adding a rendering dependency. It uses 41
constructed samples across -10 to +10 MHz, an illustrative Lorentzian centered
at +1 MHz and small deterministic sine/cosine deviations. The second panel
shows sample-minus-fit residuals. No hardware was accessed, no Qibocal
acquisition was run and no measured uncertainty is supplied.
The simpler SVG report picture is a separate original companion illustration.
