Tutorial: interactive calibration on the access node
====================================================

In :doc:`interactive`, Copilot runs on your computer and reaches the cluster
through SSH. Here, Copilot runs directly on the cluster's access node, and the
experiments still go through SLURM. This is useful when you already work in a
terminal on the cluster, or cannot install the tools on your computer.

The steps mirror the first tutorial, with the same simulated qutrit platform.
Read it first if you are new to the tools: this page explains only what
changes. The placeholders are the same:

.. list-table::
   :header-rows: 1
   :widths: 25 45 30

   * - Placeholder
     - Meaning
     - Example
   * - ``ACCESS_NODE``
     - Host name of the cluster login node
     - ``login.cluster.org``
   * - ``USER``
     - Your user name on the cluster
     - ``jdoe``
   * - ``PARTITION``
     - SLURM partition where the simulator may run
     - ``sim``

1. Log in to the access node
----------------------------

.. code-block:: sh

   ssh USER@ACCESS_NODE

Every following command runs there. No SSH key setup is needed, since the
tools do not open SSH connections in this setup.

2. Install the tools on the access node
---------------------------------------

Install ``uv``, then log out and back in so that the command is found:

.. code-block:: sh

   curl -LsSf https://astral.sh/uv/install.sh | sh

Get the project into your home folder and install it:

.. code-block:: sh

   git clone https://github.com/qiboteam/qibocal-mcp-servers.git
   cd qibocal-mcp-servers
   uv sync
   realpath .venv/bin/qibocal-agent

Keep the printed path for later. ``uv`` also installs a suitable Python, so
the cluster's own ``python3`` version does not matter here.

Finally, install Copilot CLI:

.. code-block:: sh

   npm install -g @github/copilot

.. tip::

   If ``npm`` is missing, ask your administrator for Node.js. On some
   clusters it takes a ``module load nodejs`` command.

3. Get the simulator
--------------------

Clone the simulator into your home folder:

.. code-block:: sh

   cd ~
   git clone git@github.com:qiboteam/qibolab-simulator.git

.. note::

   The simulator repository is not public yet. Ask the Qibo team for access
   if the clone is refused.

4. Tell Qibocal about your setup
--------------------------------

From the ``qibocal-mcp-servers`` folder:

.. code-block:: sh

   .venv/bin/qibocal-agent config set default.platform qutrits
   .venv/bin/qibocal-agent config set default.platforms_dir '~/qibolab-simulator/examples/platforms'
   .venv/bin/qibocal-agent config set default.partition PARTITION

Compared with the first tutorial, the only difference is that there is no
``ssh``: you are already on the cluster. The server and your dependencies get
their own Python environment, in ``~/.cache/qibocal/agents/envs/default``,
separate from the project's ``.venv``.

Run ``.venv/bin/qibocal-agent config`` alone to see where the configuration
file is and what it contains.

.. tip::

   ``venv`` selects another environment, for example one you already have,
   such as ``'~/envs/qibo'``. It is then used in place, keeping its packages as
   far as possible; see :ref:`existing-environments`. To keep the reports
   elsewhere than ``~/.local/share/qibocal/agents/runs/default``, set
   ``output``, for example
   ``.venv/bin/qibocal-agent config set default.output '~/calibration-runs'``.

5. Add the platform's dependencies
----------------------------------

This step is the same as in the first tutorial:

.. code-block:: sh

   .venv/bin/qibocal-agent config set default.dependency \
     '["qibolab-simulator @ file:///home/USER/qibolab-simulator"]'

The configuration file now looks like this:

.. code-block:: json

   {
     "agents": {
       "default": {
         "platform": "qutrits",
         "platforms_dir": "~/qibolab-simulator/examples/platforms",
         "partition": "PARTITION",
         "dependency": [
           "qibolab-simulator @ file:///home/USER/qibolab-simulator"
         ]
       }
     }
   }

6. Prepare the environment
--------------------------

Create the environment once, so that Copilot does not give up waiting later:

.. code-block:: sh

   .venv/bin/qibocal-agent mcp default < /dev/null

It installs Qibocal, the server and the simulator, then exits by itself.

.. note::

   The experiments run on compute nodes, which use this environment and your
   reports folder. Both live in your home folder, so it must be visible from
   the compute nodes, as on most clusters. Ask your administrator if unsure.

7. Register the server in Copilot
---------------------------------

Edit ``~/.copilot/mcp-config.json`` on the access node, with the path printed
in step 2:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "command": "/home/USER/qibocal-mcp-servers/.venv/bin/qibocal-agent",
         "args": ["mcp"]
       }
     }
   }

8. Calibrate
------------

Start Copilot on the access node:

.. code-block:: sh

   copilot

Log in with ``/login`` the first time, and check with ``/mcp show`` that the
``qibocal`` server is listed. Then ask for a calibration exactly as in
:doc:`the first tutorial <interactive>`. Each experiment is still a SLURM job,
so expect queue waits.

.. warning::

   As in the first tutorial, check that Copilot can see the report images
   before letting it accept any update.

9. Browse the results
---------------------

Reports are in ``~/.local/share/qibocal/agents/runs/default/sessions/`` on the
cluster. To browse them with Qibocal Report, start the report server on the
access node:

.. code-block:: sh

   .venv/bin/qibocal-agent report

It prints a local address, by default ``http://127.0.0.1:8000``. That address
is local to the access node, so forward it to your computer. In a second
terminal on your computer, run:

.. code-block:: sh

   ssh -N -L 8000:127.0.0.1:8000 USER@ACCESS_NODE

While both commands run, register ``http://127.0.0.1:8000`` in the Qibocal
Report interface on your computer.

10. Named configurations
------------------------

Named configurations, switching within a session and changing the starting
configuration work exactly as in :doc:`the first tutorial <interactive>`.
Just leave out ``ssh``.

Next steps
----------

* Let the agent calibrate on its own: :doc:`autonomous`. On the access node,
  it uses this same configuration.
* All configuration settings: :doc:`../guides/configuration`.
