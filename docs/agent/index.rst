Orchestration and autonomous agent
==================================

The ``qibocal-agent`` package reaches the :doc:`MCP servers <../mcp/index>`
from wherever you are. Its base installation is standard-library only, so
putting it on a laptop costs nothing; four commands cover the workflow:

* ``qibocal-mcp-remote`` — serve the calibration MCP over stdio for any
  interactive client, launching the real server locally or through SSH and
  provisioning its Python environment on the way when asked.
* ``qibocal-agent`` — an autonomous Pydantic AI calibration agent: give it a
  bounded objective and it plans, executes, inspects report images and
  explicitly accepts or rejects fitted updates. It fetches the same
  ``calibrate`` policy that interactive clients get from the server's prompt.
* ``qibocal-report-server`` — browse the generated calibration reports,
  locally or through a private SSH tunnel.
* ``qibocal-mcp-config`` — store the few things you know about your setup
  once, so every launch reduces to a name.

Getting started is three steps: install, tell it what you know, launch.
Everything else — managed environments, platform repositories, scheduler
options — has a working default and is documented as an advanced feature.

.. toctree::
   :maxdepth: 2

   installation
   getting-started
   interactive
   ../tutorials/index

Reusable Pydantic AI sessions
-----------------------------

``open_calibration_agent(settings, model, *, model_base_url=None)`` is an async
context manager yielding a Pydantic AI ``Agent``. Install the ``agent`` extra
and configure your provider as for the one-shot CLI. Pass a model identifier
or a configured Pydantic AI ``Model`` object; ``model_base_url`` is only valid
with an identifier. ``DeploymentSettings`` contains hardware/deployment options,
not model settings, and does not read the configuration file automatically.

.. code-block:: python

   import asyncio

   from qibocal_agent.agent import open_calibration_agent
   from qibocal_agent.deployment import DeploymentSettings

   async def calibrate():
       settings = DeploymentSettings(platform="YOUR_PLATFORM", output="runs")
       async with open_calibration_agent(settings, "openai:gpt-4.1") as agent:
           result = await agent.run(
               "Inspect qubit 0's drive-frequency spectroscopy. Sweep no wider "
               "than 20 MHz, amplitude at most 0.1, at most 100 shots. Run at "
               "most two protocols across this session. Inspect plots before "
               "accepting updates; stop on errors or insufficient evidence."
           )
           print(result.output)
           result = await agent.run(
               "Summarize the evidence and remaining budget; do not run "
               "another protocol or apply another update.",
               message_history=result.all_messages(),
           )
           print(result.output)

   asyncio.run(calibrate())

Replace the illustrative target and limits with operator-approved values.
All turns must stay inside the context: it retains the live MCP server or
gateway and run handles until exit. Retain ``result.all_messages()`` for each
subsequent turn; message history alone cannot restore run handles in a new
context. Entering the context performs discovery, which can connect hardware
or queue a compute job. The existing ``qibocal-agent`` CLI remains one-shot.
