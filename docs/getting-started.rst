Getting started with the agent
==============================

Before launching, complete the minimal setup in :doc:`installation`, confirm
exclusive instrument access and read :doc:`guides/safety`. There is no server
command or MCP client configuration to prepare: the agent starts its own
subprocess.

Launch a bounded objective
--------------------------

.. code-block:: sh

   uv run qibocal-agent --model openai:gpt-4.1 --platform YOUR_PLATFORM \
     "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
      than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
      Run at most two protocols. Inspect plots before accepting an update.
      Stop on errors or insufficient evidence."

Use a vision- and tool-capable model, and replace target ``0`` and the
illustrative limits with values approved for your hardware. The objective
must contain hardware limits, an execution budget and stopping criteria.
Model instructions are not enforced hardware limits.

The agent reads context, lets the model plan, executes one protocol at a time,
inspects returned images and decides whether to update or try another experiment.
There is no hard-coded calibration sequence, fit acceptance threshold or
workflow engine. The server does not enforce the natural-language budget.
The default output directory is ``runs``.

Retain the outcome
------------------

Keep the run's artifact location from the final summary. Run handles expire
when the agent's server or gateway exits, even though the files remain on disk.
Accepted updates affect subsequent acquisitions and save platform snapshots;
promoting a snapshot to your source platform definition is a separate manual
operation. See :doc:`guides/artifacts`.

Continue with :doc:`tutorials/local-calibration` for evidence inspection or
:doc:`tutorials/ssh-slurm` for remote hardware. Provider selection and stored
defaults are covered separately in :doc:`guides/model-providers` and
:doc:`guides/configuration`; neither is required for the standard first run.
