Tutorial: interactive calibration
=================================

In this tutorial you calibrate a simulated qutrit platform running on a
cluster, chatting with GitHub Copilot CLI on your own computer. You only need
the :doc:`minimal installation <../agent/installation>`.

The simulator behaves like real hardware from the tools' point of view, so
every step carries over unchanged to an actual chip. At the end, you add a
second configuration for a real platform and switch between the two.

Two machines are involved: **your computer**, where you type every command and
chat with Copilot, and **the cluster**, where the calibration server, your
platform files and the reports live. Each step says where it runs, and each
path says which machine it belongs to.

Before you start, collect these values. The tutorial uses the placeholders on
the left:

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

1. Connect to the cluster without a password
--------------------------------------------

*On your computer.* The tools open SSH connections on their own, so they cannot type a password.
Create a key and copy it to the cluster:

.. code-block:: sh

   ssh-keygen -t ed25519
   ssh-copy-id USER@ACCESS_NODE

Press Enter at every question of ``ssh-keygen``. ``ssh-copy-id`` asks for your
cluster password one last time.

Then give the cluster a short name. Open ``~/.ssh/config`` in your editor,
creating it if needed, and add:

.. code-block:: text

   Host mycluster
       HostName ACCESS_NODE
       User USER

Check that it works. This must print ``ok`` without asking anything:

.. code-block:: sh

   ssh -o BatchMode=yes mycluster echo ok

2. Check the cluster
--------------------

*On your computer.* This command runs on the cluster through SSH:

.. code-block:: sh

   ssh mycluster 'python3 --version; git --version'

Python must be version 3.12 or 3.13.

.. warning::

   If Python is older, ask your administrator how to get a newer ``python3``.
   On some clusters this takes a ``module load`` command.

3. Get the simulator
--------------------

*On your computer.* Clone the simulator into your home folder on the cluster:

.. code-block:: sh

   ssh mycluster 'git clone git@github.com:qiboteam/qibolab-simulator.git'

Its example platforms are now on the cluster, in
``~/qibolab-simulator/examples/platforms``. This tutorial uses the one called
``qutrits``.

.. note::

   The simulator repository is not public yet. Ask the Qibo team for access
   if the clone is refused.

4. Tell Qibocal about your setup
--------------------------------

*On your computer.* Qibocal keeps your setup in a configuration file on your
computer, so you never repeat it. Run these commands from the
``qibocal-mcp-servers`` folder:

.. code-block:: sh

   .venv/bin/qibocal-mcp-config set agent.platform qutrits
   .venv/bin/qibocal-mcp-config set agent.platforms_dir '~/qibolab-simulator/examples/platforms'
   .venv/bin/qibocal-mcp-config set agent.ssh mycluster
   .venv/bin/qibocal-mcp-config set agent.partition PARTITION

Each setting answers one question:

* ``platform``: which platform to calibrate;
* ``platforms_dir``: the folder on the cluster that contains it;
* ``ssh``: how to reach the cluster;
* ``partition``: where to run the experiments.

The quotes around the folder keep ``~`` as it is: it then refers to your
home folder *on the cluster*, not on your computer.

To see where the file is and what it contains, run the command alone:

.. code-block:: sh

   .venv/bin/qibocal-mcp-config

You can edit that file directly instead of using ``set``.

.. tip::

   Reports go to ``~/.local/share/qibocal/runs/default`` on the cluster. To
   keep them elsewhere, set ``output`` to a folder on the cluster, for example
   ``.venv/bin/qibocal-mcp-config set agent.output '~/calibration-runs'``.
   With SLURM, choose a folder the compute nodes can also see.

5. Add the platform's dependencies
----------------------------------

*On your computer.* Most platforms need extra Python packages, such as
instrument drivers. List them under ``dependency``, and they are installed on
the cluster for you.
The format is the same as the dependencies in a ``pyproject.toml`` file.

The simulator is itself such a package. Install it from your clone:

.. code-block:: sh

   .venv/bin/qibocal-mcp-config set agent.dependency \
     '["qibolab-simulator @ file:///home/USER/qibolab-simulator"]'

The configuration file now looks like this:

.. code-block:: json

   {
     "agent": {
       "platform": "qutrits",
       "platforms_dir": "~/qibolab-simulator/examples/platforms",
       "ssh": "mycluster",
       "partition": "PARTITION",
       "dependency": [
         "qibolab-simulator @ file:///home/USER/qibolab-simulator"
       ]
     }
   }

.. tip::

   A ``file://`` address needs the full path of the folder *on the cluster*,
   without ``~``. For a real chip, list its drivers instead, for example
   ``"qibolab-qblox==0.0.4"`` for Qblox instruments.

6. Prepare the environment on the cluster
-----------------------------------------

*On your computer.* The first launch installs Qibocal, the server and your
dependencies on the cluster, which takes a few minutes. Do it once now, so that Copilot does not
give up waiting later:

.. code-block:: sh

   .venv/bin/qibocal-mcp-remote < /dev/null

You see installation messages, and the command exits by itself when done.
Later launches reuse the environment and start quickly. When you change the
dependencies, the next launch installs the new ones.

.. note::

   This assumes your home folder on the cluster is also visible from the
   compute nodes, as on most clusters. Ask your administrator if unsure.

7. Register the server in Copilot
---------------------------------

*On your computer.* Open ``~/.copilot/mcp-config.json`` in your editor,
creating the folder and the file if needed. Add the path printed at the end of the installation:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "command": "/full/path/to/qibocal-mcp-servers/.venv/bin/qibocal-mcp-remote"
       }
     }
   }

That is all Copilot needs: every setting comes from Qibocal's configuration
file.

.. tip::

   Other assistants have an equivalent file, and the same ``command`` works
   there. If Copilot does not list the tools, or stops waiting for a queued
   job, see :doc:`../agent/advanced` for the full set of client options.

8. Calibrate
------------

*On your computer.* Start Copilot:

.. code-block:: sh

   copilot

The first time, log in with ``/login``. Then type ``/mcp show``: the
``qibocal`` server should be listed. Now ask for a calibration, for example:

   Calibrate with the qibocal tools, starting from the calibrate tool.
   Objective: find the drive frequency of qubit 0 with spectroscopy. Run at
   most two protocols, inspect the plots before accepting any update, and stop
   on errors.

Copilot first reads the calibration instructions and the platform context.
Then it runs one experiment at a time, shows you the results and explains its
choices. It asks your permission before each tool call: read what it wants to
run before approving. Each experiment is a SLURM job, so expect queue waits.

.. tip::

   The calibration instructions are also offered as an MCP prompt called
   ``calibrate``. Assistants that support MCP prompts show it as a slash
   command. Copilot CLI currently ignores MCP prompts, which is why the
   request above names the tool instead.

.. warning::

   The model must actually see the report images to judge a fit. After the
   first experiment, ask Copilot to describe the plot it received. If it
   cannot, do not let it accept updates, and use an assistant or model that
   displays MCP images.

.. note::

   On real hardware, always state the limits approved for your device, such
   as maximum amplitudes, frequency ranges and shot counts. The instructions
   ask the model to respect them, but nothing enforces them on the hardware.

9. Find the results
-------------------

*On your computer.* Reports are saved on the cluster, in
``~/.local/share/qibocal/runs/default/sessions/``, one folder per calibration
session. Copilot's summary tells you the exact folder. Accepted updates are
saved as new snapshots there: your platform files are never overwritten.

To browse the reports in Qibocal Report, run from the project folder:

.. code-block:: sh

   .venv/bin/qibocal-report-server

It opens an SSH tunnel to the cluster and prints a local address to register
in the Qibocal Report interface. Keep it running while you browse. See
:doc:`../guides/artifacts` for the folder layout and how to read a report.

10. Several setups: named configurations
----------------------------------------

*On your computer.* The ``agent`` section is your default configuration. You can store more
configurations, each under its own name, in the ``agents`` section. For
example, to add a real chip called ``platinum``:

.. code-block:: sh

   .venv/bin/qibocal-mcp-config set agents.platinum.platform qw5q_platinum
   .venv/bin/qibocal-mcp-config set agents.platinum.platforms_dir '~/qibolab_platforms_qrc'
   .venv/bin/qibocal-mcp-config set agents.platinum.ssh mycluster
   .venv/bin/qibocal-mcp-config set agents.platinum.partition qw5q_platinum
   .venv/bin/qibocal-mcp-config set agents.platinum.dependency '["qibolab-qblox==0.0.4"]'

A named configuration contains all its settings: it does not inherit anything
from the default one. It also gets its own environment and report folder on the
cluster, ``~/.local/share/qibocal/runs/platinum``, so the simulator and the
chip never interfere. Prepare its environment once, as in step 6:

.. code-block:: sh

   .venv/bin/qibocal-mcp-remote platinum < /dev/null

Switch within a session
~~~~~~~~~~~~~~~~~~~~~~~

You do not need to restart Copilot to change configuration. Just ask:

* *"List the qibocal configurations"* shows every stored configuration and
  which one is active.
* *"Switch to the platinum configuration"* closes the current server and
  starts the selected one. Ask to switch to the default configuration to go
  back.

Switching ends the current calibration session, so finish or reject pending
updates first. Configurations you add while Copilot is running can be used
right away.

Change the starting configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Copilot starts with the default configuration. To start with a named one
instead, add its name to the registration in ``~/.copilot/mcp-config.json``:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "command": "/full/path/to/qibocal-mcp-servers/.venv/bin/qibocal-mcp-remote",
         "args": ["platinum"]
       }
     }
   }

Next steps
----------

* Run Copilot on the access node instead of your computer:
  :doc:`access-node`.
* Let the agent calibrate on its own: :doc:`autonomous`.
* All configuration settings: :doc:`../guides/configuration`.
* Client options and settings on the command line: :doc:`../agent/advanced`.
