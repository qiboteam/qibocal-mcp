Agent installation
==================

Minimal installation
--------------------

The orchestration package runs on your own machine — a laptop is fine. Its
base installation has no third-party dependencies; the ``agent`` extra adds
the Pydantic AI stack for the autonomous agent:

.. code-block:: sh

   pip install "./packages/agent[agent]"   # from a repository checkout
   export OPENAI_API_KEY=...

or, for a development checkout of the whole workspace, ``uv sync``.
Skip the export if your provider credentials are already configured, and skip
the extra entirely if you only want ``qibocal-mcp-remote``,
``qibocal-report-server`` and ``qibocal-mcp-config``.

Where does the hardware-facing part go?

* **Hardware reachable from this machine:** also install
  :doc:`qibocal-mcp <../mcp/installation>` here (``uv sync`` installs both),
  with Chrome, drivers and ``QIBOLAB_PLATFORMS`` configured.
* **Hardware behind SSH/SLURM:** nothing to install remotely by hand. An SSH
  launch provisions a managed server environment on the access node
  automatically (it needs ``python3`` 3.12/3.13, ``git`` and ``pip``/``uv``
  there), or uses an interpreter your site already provides via
  ``--server-python``. Provisioning ships the ``qibocal-mcp`` source from your
  checkout or local installation — never your configuration or credentials.

Continue to :doc:`getting-started`. Only use the notes below if the standard
setup does not match your site.

Advanced installation notes
---------------------------

If platform paths or credentials are not already configured on the execution
host:

.. code-block:: sh

   export QIBOLAB_PLATFORMS=/absolute/path/to/your/platforms

For another model provider, use its recommended environment or credential
settings; the agent extra also supports GitHub Copilot subscriptions and
OpenWebUI (see :doc:`../guides/model-providers`). Do not put API keys in
objectives or ``agent.json``.

If Chrome is missing on the execution host, install it for Kaleido's Plotly
image export with ``uv run plotly_get_chrome -y``, or use ``devenv shell``,
which configures Python 3.12, Nix-managed Chrome and ``BROWSER_PATH`` and
syncs the workspace. Chrome and system-level instrument drivers remain
site-managed; managed environments do not install them.

Managed environments, platform repositories, Python driver dependencies and
cluster path requirements are advanced deployment features with their own
reference: see :doc:`../guides/configuration` and :doc:`../guides/networking`.
The short version: an SSH launch without ``--server-python`` provisions a
managed environment named ``qibocal-mcp`` on the access node and reuses it on
later launches; ``--venv``, ``--platform-repo`` and ``--dependency`` customize
what gets provisioned.
