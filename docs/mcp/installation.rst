MCP server installation
=======================

Minimal installation
--------------------

Install ``qibocal-mcp`` on the execution host: the machine that reaches the
instruments (direct mode) or the SLURM access node (queued mode). You need
Python 3.12 or 3.13, Chrome for report-image export, and a working Qibolab
platform with its hardware drivers. Assume ``QIBOLAB_PLATFORMS`` already
points to your platform definitions.

From a checkout of this repository:

.. code-block:: sh

   uv sync

This installs both workspace packages, including Qibocal and the server
dependencies. Alternatively, install just the server package:

.. code-block:: sh

   pip install ./packages/mcp

No agent extra, model provider or credentials are needed. Continue with
:doc:`getting-started` to configure your MCP client's stdio subprocess.

If you cannot install anything on the execution host yourself, the
orchestration package can provision a managed environment there over SSH;
see :doc:`../agent/interactive`.

Advanced installation and customization
---------------------------------------

Missing prerequisites
~~~~~~~~~~~~~~~~~~~~~

If your site has not configured the platform search path:

.. code-block:: sh

   export QIBOLAB_PLATFORMS=/absolute/path/to/your/platforms

The server's ``--platforms-dir`` option sets the same variable for one launch.
If Chrome is missing, install it for Kaleido's Plotly image export:

.. code-block:: sh

   uv run plotly_get_chrome -y

Alternatively, ``devenv shell`` configures Python 3.12, installs Nix-managed
Chrome, sets ``BROWSER_PATH`` for Kaleido and syncs the whole workspace.

Supply your own platform definitions and use their actual qubit IDs.
Qibocal's test-only ``mock`` platform is not included in its installed package.
Chrome and system-level instrument drivers remain site-managed.

Queued execution
~~~~~~~~~~~~~~~~

Install the server on the access node, on shared storage, so compute nodes
see the same environment. The Python environment, working directory, platform
definitions and output directory must be available at identical absolute paths
on access and compute nodes; Chrome and drivers must be available on compute
nodes, where reports and acquisitions actually run. There is no automatic
node-local staging or compute-node provisioning.

``--worker-python`` selects a different interpreter for compute-node jobs;
it defaults to the gateway's own interpreter. Both must contain the server.
``--workdir`` sets the working directory of those jobs.

The pinned Qibocal API
~~~~~~~~~~~~~~~~~~~~~~

Qibocal is pinned to commit
``ae0e797a8c93a1110737f7f75586e628e4a2e543`` on the ``protodev`` branch of
`qiboteam/qibocal#1722 <https://github.com/qiboteam/qibocal/pull/1722>`_.
The pin is a PEP 508 direct Git requirement in ``project.dependencies``, not a
uv-only source override, so pip and built distributions honor the same commit.
Protocol execution calls ``Executor.__call__()`` with automatic fitted updates
disabled; accepted updates call ``Executor.update(completed)`` explicitly.
Changing the pin requires checking those APIs and the saved artifact format.
Session and protocol comments use its ``Note`` model and ``notes.json`` format.
