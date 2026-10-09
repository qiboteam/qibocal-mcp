Development and documentation builds
====================================

Source layout
-------------

* ``src/qibocal_mcp/server.py``: direct platform server and runtime entry point.
* ``src/qibocal_mcp/agent.py``: model instructions, local/SSH transport and loop.
* ``src/qibocal_mcp/models.py``: native Copilot and OpenWebUI model selection.
* ``src/qibocal_mcp/slurm.py``: gateway serialization, dispatch and accepted-state
  handoff.
* ``src/qibocal_mcp/worker.py``: one internal operation per allocation, with
  JSON on stdin/stdout. Workers do not read user configuration.
* ``src/qibocal_mcp/config.py``: validated persistent defaults and management CLI.
* ``tests/``: ``unittest`` coverage for configuration, transports, direct server
  behavior and queued state handoff.

Review :doc:`installation` before changing the pinned Qibocal API.
Preserve stdout for MCP/worker protocols, and keep model API calls out of the
server. Operational invariants are described in :doc:`guides/architecture`
and :doc:`guides/safety`.

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

The site uses native reStructuredText, Sphinx's built-in Alabaster theme and no
additional extensions or theme packages. The required optional ``docs`` extra
is ``sphinx>=8,<9``.

.. code-block:: sh

   uv sync --extra agent --extra docs
   uv run sphinx-build -W --keep-going -b html docs docs/_build/html

Open ``docs/_build/html/index.html``. ``-W`` treats warnings as failures;
``--keep-going`` reports all warnings before the build exits.
Sphinx reads static documentation without importing project modules, connecting
to hardware or requesting model credentials. Generated output is not source
and should remain untracked.

Pages are organized by purpose: installation/getting started, practical
tutorials, conceptual guides and CLI/MCP reference. Add new pages to a toctree
so readers can navigate to them.

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
