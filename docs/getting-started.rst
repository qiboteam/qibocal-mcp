Getting started
===============

Choose a client
---------------

Use another MCP client for explicit tool calls, or the optional agent for a
model-directed loop. Do not start a standalone server and expect the agent to
attach to it: the agent starts its own subprocess.

Before either approach, complete :doc:`installation`, confirm exclusive
instrument access and read :doc:`guides/safety`.

Launch a stdio server
---------------------

.. code-block:: sh

   export QIBOLAB_PLATFORMS=/path/to/your/platforms
   uv run qibocal-mcp --platform YOUR_PLATFORM --output runs

Configure your MCP client to launch that command with this repository as its
working directory. The process communicates over stdio; it does not provide an
HTTP URL. Running it in a terminal alone does not initiate a calibration.
There are exactly two tools, ``execute_protocol`` and ``update_platform``,
plus the ``qibocal://context`` resource.

Read the context first to discover qubit IDs, protocol definitions, units and
update support. For example, after checking that target ``0`` exists and that
these parameters are appropriate, a client can call ``execute_protocol`` with:

.. code-block:: json

   {
     "protocol": "qubit_spectroscopy",
     "parameters": {
       "freq_width": 20000000,
       "freq_step": 200000,
       "nshots": 100
     },
     "targets": [0]
   }

The returned metadata, tables and MCP image content let you inspect the fit
before deciding whether to call ``update_platform`` with the returned
``run_id``. A successful acquisition is not by itself justification for an
update. See :doc:`reference/mcp` for target arrays and response details.

Launch an agent
---------------

.. code-block:: sh

   export OPENAI_API_KEY=...
   uv run qibocal-agent --model openai:gpt-4.1 --platform YOUR_PLATFORM \
     "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
      than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
      Run at most two protocols. Inspect plots before accepting an update.
      Stop if the evidence is insufficient."

Use a vision-capable model, and replace target ``0`` and the illustrative
limits with values approved for your hardware. The objective must contain
hardware limits, an execution budget and stopping criteria.

The agent reads context, lets the model plan, executes one protocol at a time,
inspects returned images and decides whether to update or try another experiment.
There is no hard-coded calibration sequence, fit acceptance threshold or
workflow engine. The server does not enforce the natural-language budget.

Keep the run's artifact location from the final summary. Run handles expire
when the server or gateway exits, even though the files remain on disk.
Continue with :doc:`tutorials/local-calibration` or
:doc:`tutorials/ssh-slurm`.
