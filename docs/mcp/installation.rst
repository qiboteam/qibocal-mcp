MCP server installation
=======================

Minimal installation
--------------------

For a standard local setup, use a checkout of this repository with ``uv``,
Python 3.12 or 3.13, Chrome, and a working Qibolab platform and hardware
drivers. Assume ``QIBOLAB_PLATFORMS`` already points to your platform
definitions.

.. code-block:: sh

   uv sync

This installs Qibocal and the server's Python dependencies. No agent extra,
model provider or credentials are needed. Continue with
:doc:`getting-started` to configure your MCP client's stdio subprocess.

Advanced installation and customization
---------------------------------------

Missing prerequisites
~~~~~~~~~~~~~~~~~~~~~

If your site has not configured the platform search path:

.. code-block:: sh

   export QIBOLAB_PLATFORMS=/absolute/path/to/your/platforms

If Chrome is missing, install it for Kaleido's Plotly image export:

.. code-block:: sh

   uv run plotly_get_chrome -y

Alternatively, ``devenv shell`` configures Python 3.12, installs Nix-managed
Chrome, sets ``BROWSER_PATH`` for Kaleido and syncs all extras. Those extra
packages are not needed for a standalone server.

Supply your own platform definitions and use their actual qubit IDs.
Qibocal's test-only ``mock`` platform is not included in its installed package.
Chrome and system-level instrument drivers remain site-managed; managed
Python environments do not install them.

Managed environments and dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A standalone server uses its current interpreter unless you supply
``--venv NAME_OR_PATH``. With that option, it provisions an isolated
environment containing the server and pinned Qibocal. There is no implicit
managed environment for standalone launches, including launches over SSH.

Named environments live on the execution host under
``$XDG_CACHE_HOME/qibocal-mcp/envs/NAME`` or ``~/.cache/qibocal-mcp/envs/NAME``.
Absolute paths, ``./`` and ``../`` paths are resolved on that host; ``--venv``
also expands ``~`` there. Managed deployment requires POSIX, Python 3.12/3.13,
Git, and ``pip``/``ensurepip``, ``uv`` or external ``pip>=22.3``.
Environments are reused and dependencies reconciled when source or dependency
state changes. Installation is additive; removed configured dependencies
are not uninstalled. Broken or custom environments are not automatically
deleted or recreated; provisioning errors stop startup.

Use repeatable ``--platform-repo`` options for trusted platform repositories
and ``--dependency REQUIREMENT`` for Python drivers or additional requirements.
Platform repository dependencies are discovered and installed automatically.
Explicit ``--platforms`` paths are searched first, repository roots next,
and inherited ``QIBOLAB_PLATFORMS`` last when explicit paths are absent.
With neither option, the environment variable is untouched.
Cached clones are reused without automatic pull/fetch/reset; pin a commit
for reproducibility. Trust all package sources, since installation can
execute code.

Without ``--venv``, repository/dependency options intentionally install into
the selected existing interpreter. Prefer ``--venv`` for isolation.
For all dependency-selection, path, cache and persistent-default rules,
see :doc:`../guides/configuration`.

Queued execution
~~~~~~~~~~~~~~~~

Install the standalone server on the access node, then provision a managed
environment on shared storage with ``--venv`` or use an externally managed
shared interpreter. The environment, working directory, cached source
snapshots and repository clones, platforms and output directory must be
available at identical absolute paths on access and compute nodes.
Configure ``XDG_CACHE_HOME`` on shared storage too; ``--venv`` alone does
not move the cache. No compute-node provisioning or node-local staging occurs.
Chrome and drivers must be available on compute nodes.

``--worker-python`` selects an externally managed shared worker interpreter.
It cannot coexist with ``--venv``; with ``--venv``, gateway and workers use
the same managed environment. If repository/dependency options are used
with a distinct explicit worker interpreter, dependencies are installed into
both gateway and worker interpreters. Both must already contain the server;
this does not install the core server into externally managed interpreters.
See :doc:`getting-started` for a queued gateway command and
:doc:`../guides/networking` for the execution topology.
