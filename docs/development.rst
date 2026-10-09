Development and documentation builds
====================================

Source layout
-------------

* ``src/qibocal_mcp/agent.py``: model instructions, local/SSH transport and loop.
* ``src/qibocal_mcp/models.py``: native Copilot and OpenWebUI model selection.
* ``src/qibocal_mcp/server.py``: direct platform server and runtime entry point.
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
existing MCP commands, configuration paths and caches keep their names.
Installed-source deployment reads ``qibocal-agent`` distribution metadata.
Review :doc:`installation` before changing the pinned Qibocal API.
Preserve stdout for MCP/worker protocols, and keep model API calls out of the
server. Operational invariants are described in :doc:`guides/architecture`
and :doc:`guides/safety`.

Provisioning invariants
----------------------

Keep deployment independent of the agent extra and model credentials. The
agent resolves the provider before deployment/hardware, and the SSH bootstrap
uses the standard library to transfer only server source and package metadata
from the launching checkout or installed distribution. The installed-source
fallback must preserve core dependencies and the Qibocal Git pin from distribution
metadata without requiring a local checkout, including for a wheel-installed
agent. Reconstruct minimal installation metadata from ``distribution.requires``.
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

Caches belong to the execution host under ``qibocal-mcp``, not notebook
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
with agent installation, getting started and tutorials; ``mcp/index`` has
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
