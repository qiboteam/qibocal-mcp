Development and documentation builds
====================================

Workspace layout
----------------

The repository is a uv workspace with two distributions:

``packages/mcp`` — the ``qibocal-mcp`` distribution (module ``qibocal_mcp``),
every MCP server and nothing else:

* ``server.py``: responsive direct-server frontend with background hardware
  startup, and the ``qibocal-mcp`` click command (plain options, no
  configuration file, direct or ``--partition`` gateway mode). It stays a
  single command, as the package has nothing else to expose.
* ``runtime.py``: internal main-thread hardware server, also used directly
  inside SLURM workers.
* ``slurm.py``: gateway serialization, dispatch and accepted-state handoff.
* ``worker.py``: one internal operation per allocation, with JSON on
  stdin/stdout. Workers do not read user configuration.
* ``calibration.py``: the calibration policy, registered on every server as
  the ``calibrate`` MCP prompt and the identically named alias tool
  (for interfaces that ignore MCP prompts), plus context/tool identifiers.
* ``artifacts.py``: incremental task-backed Qibocal report storage shared by
  direct execution and successive SLURM workers.
* ``notes.py``: atomic Qibocal note appends using the report server's
  per-directory ``.notes.lock``.
* ``sessions.py``: paired calibration checkpoints and repository revision
  selection, with explicit configured-remote fetching and no checkout.
* ``locking.py``: the shared advisory file lock.
* ``targets.py``: Qibocal-compatible wire types without importing the
  calibration stack.

``packages/agent`` — the ``qibocal-agent`` distribution (module
``qibocal_agent``, the import name of the same package), the orchestration;
its base dependencies are click and FastMCP, without Qibocal, and the
``agent`` extra adds the Pydantic AI stack:

* ``cli.py``: the ``qibocal-agent`` click group and its subcommands ``run``,
  ``mcp``, ``report`` and ``config``. Heavy imports stay inside the
  subcommands, so ``--help`` remains fast.
* ``deployment.py``: ``DeploymentSettings`` and the ``launch_for`` stdio
  launch contract (local or SSH).
* ``environment.py``: execution-host environment bootstrap, cached
  source/repositories and dependency provisioning; also shipped standalone
  over SSH, so it must remain a single standard-library file. Its
  ``source_payload`` ships the ``qibocal-mcp`` source, found by
  ``server_module``: the locally installed distribution, the repository
  checkout around the code, or else the server folder of the source
  ``qibocal-agent`` was installed from (``direct_url.json``): the same Git
  commit, exported once into the local cache, or the sibling folder of a local
  installation. ``qibocal-agent`` must therefore stay deployable on its own,
  without depending on ``qibocal-mcp`` and its Qibocal stack.
* ``remote.py``: the ``qibocal-agent mcp`` proxy for interactive clients.
  It owns one upstream server, forwards its tools, prompts and resources via
  FastMCP's ``ProxyProvider``, and adds ``list_configurations`` and
  ``switch_configuration``. Switching closes the previous server before
  launching the next, since both may target the same instruments; a failed
  launch leaves the proxy up with only the switching tools.
* ``agent.py``: Pydantic AI transport adapter, reusable
  ``open_calibration_agent`` async context manager and the one-shot run. The
  calibration policy is fetched from the server's ``calibrate`` prompt, not
  embedded.
* ``models.py``: GitHub Copilot subscription and OpenWebUI model selection.
* ``config.py``: the validated configuration file, ``agents.json``, holding
  named configurations (``default`` when no name is given), and ``resolve``,
  which merges built-ins, a stored configuration and explicit CLI values.
* ``report.py``: the managed ``qibocal-agent report`` launcher and tunnel.

Each package has its own ``tests/`` directory with ``unittest`` coverage;
``packages/mcp/tests`` requires the Qibocal stack, while
``packages/agent/tests`` is hardware-free.

Configuration lives in ``qibocal/agents.json``; caches use ``qibocal/agents``
under their respective XDG roots. Review :doc:`mcp/installation` before
changing the pinned Qibocal API. Preserve stdout for MCP/worker protocols,
and keep model API calls out of the servers. Operational invariants are
described in :doc:`guides/architecture` and :doc:`guides/safety`.
Incremental report saves must not call ``Output.dump`` on existing histories:
it replaces note files from possibly stale in-memory lists. Save metadata,
history and platform separately; note appends reload under ``.notes.lock`` and
atomically replace only ``notes.json``.
Likewise, persist an accepted update's action through ``Task.dump``, not
``Completed.dump_parameters``, which also replaces its note history.

Provisioning invariants
-----------------------

Keep deployment and source-payload code independent of the agent extra and
model credentials: the base ``qibocal-agent`` installation must not pull the
Qibocal or Pydantic AI stacks, and ``environment.py`` itself must stay a
standard-library single file. The Pydantic AI agent resolves the provider before
deployment/hardware. The SSH bootstrap uses the standard library to transfer
only the ``qibocal-mcp`` source and package metadata, from the workspace
checkout or the locally installed distribution. The installed-distribution
fallback must preserve core dependencies and the Qibocal Git pin from
``qibocal-mcp`` distribution metadata, reconstructing minimal installation
metadata from ``distribution.requires``. Keep the pin as a PEP 508 direct Git
requirement in ``project.dependencies``, not only ``tool.uv.sources``, so pip
and built distributions retain it. The launched server receives fully resolved
flags; target-host and worker user configuration must not be read, and the
server package must keep accepting no configuration file at all.
Keep provisioning diagnostics on stderr, never the MCP/worker protocol stream.

Every configuration without an explicit server interpreter owns a managed
environment named after it, locally and through SSH. Reject ``venv``
combined with an explicit ``server_python``.
Repository/dependency installation without a managed environment intentionally
modifies the selected interpreter; document that behavior rather than implying
isolation.

Caches belong to the execution host under ``qibocal/agents``, not notebook
``qibocal/envs``. Reuse environments and fingerprint only successful dependency
state; reconcile changed source/dependencies/requirements without silently
continuing on errors or deleting/recreating broken/custom environments.
Include nested local ``-r``/``-c`` files, root packaging metadata and Git
``HEAD`` in reconciliation hashes. Managed environments must run installer
dependency checks and verify server importability before launch.
Installation is additive, not exact sync. Cached repository clones must not
automatically pull/fetch/reset. Validate relative subdirectories against
traversal and symlink escape, and discover requirements only at the documented
root/subdirectory/selected-platform locations, deduplicating files and
delegating ``-r`` includes to pip. Resolve repository and explicit dependencies
together in one resolver request, not with precedence overrides.
An explicit ``start_session(source="repository", fetch=true)`` may fetch a
source revision from an existing Git remote without updating checkout refs,
reconciling dependencies or executing historical code. Serialize fetch and
``FETCH_HEAD`` resolution for callers sharing a checkout, record the exact
commit and remote, and preserve accepted state and handles on failure.

Maintain explicit-path/repository/inherited-host platform lookup order and
CLI whole-list replacement for ``platform_repo`` and ``dependency``. Only
``venv``, the configured directories and each ``platforms_dir`` entry expand ``~``,
always on the execution host (never on the laptop); executable settings stay
literal. SLURM assumes identical absolute shared environment, source/clone
cache, working, platform and output paths, with no node-local staging.
See :doc:`guides/configuration` and :doc:`guides/networking` for the complete
user-facing contract and trust/authentication requirements.

Existing tests
--------------

With the workspace synced:

.. code-block:: sh

   uv sync
   uv run --directory packages/mcp python -m unittest discover -s tests
   uv run --directory packages/agent python -m unittest discover -s tests

or with pytest, run each package's ``tests`` directory. For changes limited to
one area, select the matching module, for example:

.. code-block:: sh

   uv run --directory packages/agent python -m unittest discover -s tests -p 'test_config.py'

Tests use synthetic callbacks and patched transports/rendering to exercise
behavior without claiming a real hardware calibration or requiring a live
SLURM cluster. Synthetic success is not validation of your instruments.

Build this documentation
------------------------

The site uses native reStructuredText and the Furo theme, following Qibolab's
Qibo branding. The ``docs`` dependency group installs Sphinx, Furo and
``sphinx-click``, which generates the CLI reference and the manual pages
from the click commands.

The light/dark Qibo logos and favicon in ``docs/_static/`` are copied unchanged
from `Qibolab <https://github.com/qiboteam/qibolab/tree/e73aebfac321f99dfa67f19d1567dd84eaf9f980/doc/source>`_.
Their Apache 2.0 license is included as ``docs/_static/LICENSE.qibolab``.
Theme colors and repository links are configured in ``docs/conf.py``.

.. code-block:: sh

   uv sync --group docs
   uv run poe docs
   uv run poe man

``poe docs`` builds the site in ``docs/_build/html``, and ``poe man`` the
manual pages ``qibocal-mcp.1`` and ``qibocal-agent.1`` in ``docs/_build/man``,
from ``docs/man/``; read one with ``man docs/_build/man/qibocal-agent.1``.
Both run ``sphinx-build`` with ``-W``, treating warnings as failures, and
``--keep-going``, reporting all of them. The CLI pages import the click
commands, which import no hardware code and need no model credentials. Generated output is not source
and should remain untracked.

The documentation mirrors the package split: ``mcp/index`` documents the
server package for any MCP client, and ``agent/index`` documents the
orchestration. The agent track goes: installation (minimal means interactive
use), the self-contained interactive tutorial on the simulator, the autonomous
agent tutorial building on its setup, then ``agent/advanced``. Basic pages only
configure through Qibocal's configuration file, register the MCP server with
``command`` alone (plus a configuration name, for the starting configuration),
and keep refinements in callouts after the main paragraph. CLI flags, client
options and deployment machinery belong to the advanced page and the guides.
Add new pages to a toctree so readers can navigate to them.

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
