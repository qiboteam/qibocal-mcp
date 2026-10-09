Installation
============

Requirements
------------

* Python 3.12 or 3.13 and ``uv``.
* Installed Qibolab platform definitions and the drivers required by your
  instruments.
* Chrome for Kaleido's Plotly image export.
* For the agent: a vision-capable Pydantic AI model and its provider credentials.
* For queued execution: SSH access, SLURM and shared paths as described in
  :doc:`guides/networking`.

Run these commands from a checkout of this repository:

.. code-block:: sh

   uv sync --extra agent
   # Skip this if Chrome is already available.
   uv run plotly_get_chrome -y

``uv sync`` alone installs the server. Only the machine running the agent
needs ``--extra agent`` and model credentials. The optional agent extra
includes the OpenAI provider; install other Pydantic AI provider extras
yourself if you select a different provider.

Alternatively, ``devenv shell`` configures Python 3.12 and syncs all extras,
following Qibolab's Python/uv setup. Chrome must still be available for image
export. The documentation extra and build commands are in :doc:`development`.

Platform and model setup
------------------------

.. code-block:: sh

   export QIBOLAB_PLATFORMS=/absolute/path/to/your/platforms
   export OPENAI_API_KEY=...

Replace the path and supply credentials through your provider's recommended
environment or credential settings. Do not put API keys in command objectives
or in ``qibocal-mcp.json``. The server itself makes no model API calls.

A platform name is required at runtime. Qibocal's test-only ``mock`` platform
is not included in its installed package; it is not a ready-to-run hardware
substitute. Use the qubit IDs discovered from your own platform.

``--platforms`` can supply ``QIBOLAB_PLATFORMS`` on the execution host.
Leaving it unset preserves that host's existing environment value.
See :doc:`guides/configuration` for persistent defaults.

Pinned Qibocal API
------------------

Qibocal is pinned to commit
``87abd3350612b72e64b09db115ee0cf22886b507`` of
`qiboteam/qibocal#1716 <https://github.com/qiboteam/qibocal/pull/1716>`_.
This project uses that commit's API, not the PR description's older examples.
Protocol execution calls ``Executor.__call__()`` with automatic fitted updates
disabled; accepted updates call ``Executor.update(completed)`` explicitly.
Changing the pin requires checking those APIs and the saved artifact format.

Cluster installation
--------------------

Install the server on the access and compute nodes (``uv sync`` suffices).
The server Python environment, project working directory, platform definitions
and output directory must be available at the same paths on both node types.
Chrome and instrument drivers must be available on compute nodes, where
reports and acquisitions actually run.
