Orchestration and autonomous agent
==================================

The ``qibocal-agent`` package lets you calibrate from wherever you are.
Install it on your own computer, and it reaches the
:doc:`MCP servers <../mcp/index>` next to the hardware for you, over SSH and
SLURM when needed.

There are two ways to calibrate:

* **Interactively**, from an AI assistant you already use, such as GitHub
  Copilot CLI. You chat with the assistant, and it runs the experiments and
  shows you the results. This is the best way to start: you see every step
  and can steer at any time.
* **Autonomously**, with ``qibocal-agent``. You give it an objective with
  hardware limits, and it plans, runs and inspects experiments on its own.

Both use the same configuration file, so the setup you make for interactive
use also works for the autonomous agent.

Start with the installation, then follow the interactive tutorial from the
beginning. It is self-contained and uses a simulator, so you can learn the
workflow without touching real hardware.

.. toctree::
   :maxdepth: 2

   installation
   ../tutorials/interactive
   ../tutorials/access-node
   ../tutorials/autonomous
   advanced
