Installation
============

Minimal installation
--------------------

This is everything you need for interactive calibration. Install it on your
own computer: nothing has to be installed by hand on the cluster.

First install ``uv``, the Python package manager used by the project:

.. code-block:: sh

   curl -LsSf https://astral.sh/uv/install.sh | sh

Close and reopen the terminal, so that the ``uv`` command is found. Then get
the project and install it:

.. code-block:: sh

   git clone https://github.com/qiboteam/qibocal-mcp-servers.git
   cd qibocal-mcp-servers
   uv sync

The commands are now in the ``.venv/bin`` folder of the project. Print the
full path of the one your assistant will launch, and keep it for later:

.. code-block:: sh

   realpath .venv/bin/qibocal-agent

Finally, you need an AI assistant that supports MCP servers. The tutorials use
GitHub Copilot CLI, which needs Node.js:

.. code-block:: sh

   npm install -g @github/copilot

Any other MCP-capable assistant works too, such as Claude Code, Cursor or
OpenCode.

Continue with :doc:`../tutorials/interactive`.

.. tip::

   The rest of this page is optional. Come back to it only if you want the
   autonomous agent, or if your setup differs from the standard one.

Advanced installation
---------------------

The autonomous agent
~~~~~~~~~~~~~~~~~~~~

``uv sync`` already installs the autonomous agent as well. It additionally
needs credentials for a model provider, which interactive use does not need:
your assistant uses its own model. For example, for OpenAI:

.. code-block:: sh

   export OPENAI_API_KEY=...

Do not put API keys in objectives or in the configuration file. GitHub Copilot
subscriptions, OpenWebUI and other providers are described in
:doc:`../guides/model-providers`. The tutorial is :doc:`../tutorials/autonomous`.

Installing without uv
~~~~~~~~~~~~~~~~~~~~~

With ``pip``, install both packages from the checkout:

.. code-block:: sh

   pip install ./packages/mcp "./packages/agent[agent]"

The server package is needed locally even when the hardware is remote: when
it prepares the environment on the cluster, the orchestration sends the
server's source from your installation. Drop ``[agent]`` if you do not need
the autonomous agent.

What runs on the cluster
~~~~~~~~~~~~~~~~~~~~~~~~

On the first launch, the orchestration creates a Python environment on the
access node and installs the server and Qibocal in it. The access node needs
``python3`` 3.12 or 3.13, ``git``, and ``pip`` or ``uv``. The environment lives
in ``~/.cache/qibocal/agents/envs/`` on the cluster and is reused afterwards.

Your platform's drivers are installed there too, when you list them as
dependencies, as the interactive tutorial shows. Chrome, which draws the report
images, and system-level instrument drivers are not installed automatically.
Ask your administrator if they are missing on the compute nodes, or run
``uv run plotly_get_chrome -y`` in a local setup.

Managed environments, platform repositories, scheduler options and shared
paths are covered in :doc:`../guides/configuration` and
:doc:`../guides/cluster`. To use an interpreter your site already provides,
see ``server_python`` there.
