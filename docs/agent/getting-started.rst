Getting started with the agent
==============================

Before launching, complete the minimal setup in :doc:`installation`, confirm
exclusive instrument access and read :doc:`../guides/safety`. There is no
server command or MCP client configuration to prepare: the agent starts its
own subprocess.

Tell it what you know
---------------------

Provide the information you have — nothing more. Each piece can be given on
the command line or stored once:

.. code-block:: sh

   qibocal-mcp-config set agent.platform YOUR_PLATFORM
   qibocal-mcp-config set agent.model openai:gpt-4.1
   # Only if the hardware is behind SSH:
   qibocal-mcp-config set agent.ssh ACCESS_NODE
   # Only if operations must go through SLURM:
   qibocal-mcp-config set agent.partition PARTITION

That is the whole decision tree: *which platform, which model, and — if not
local — how to reach it*. Stored values live in
``~/.config/qibocal/agent.json``; you can also write that JSON file directly:

.. code-block:: json

   {
     "agent": {
       "platform": "YOUR_PLATFORM",
       "model": "openai:gpt-4.1",
       "ssh": "ACCESS_NODE",
       "partition": "PARTITION"
     }
   }

Explicit CLI flags always override stored values, and ``--no-config`` ignores
the file entirely. To keep several setups side by side, use named agents
(``agents.NAME.platform`` and friends) and launch with the name; see
:doc:`../guides/configuration`.

Launch a bounded objective
--------------------------

.. code-block:: sh

   qibocal-agent \
     "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
      than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
      Run at most two protocols. Inspect plots before accepting an update.
      Stop on errors or insufficient evidence."

Use a vision- and tool-capable model, and replace target ``0`` and the
illustrative limits with values approved for your hardware. The objective
must contain hardware limits, an execution budget and stopping criteria.
Model instructions are not enforced hardware limits.

The agent reads context, lets the model plan, executes one protocol at a time,
inspects returned images and decides whether to update or try another
experiment. There is no hard-coded calibration sequence, fit acceptance
threshold or workflow engine. The server does not enforce the natural-language
budget. The default output directory is ``runs`` (on the execution host).

With ``ssh`` configured, the agent launches the server on the access node and
provisions its environment automatically on the first run; with ``partition``,
each operation becomes its own SLURM job. Nothing else changes from your side.

Retain the outcome
------------------

Keep the run's artifact location from the final summary. Run handles expire
when the agent's server or gateway exits, even though the files remain on disk.
Accepted updates affect subsequent acquisitions and save platform snapshots;
promoting a snapshot to your source platform definition is a separate manual
operation. See :doc:`../guides/artifacts`, and browse the reports with:

.. code-block:: sh

   qibocal-report-server

Continue with :doc:`../tutorials/local-calibration` for evidence inspection or
:doc:`../tutorials/ssh-slurm` for remote hardware. Prefer driving the
calibration from your own chat interface instead of the autonomous loop? See
:doc:`interactive`. Provider selection and the full settings catalogue are in
:doc:`../guides/model-providers` and :doc:`../guides/configuration`; neither is
required for the standard first run.
