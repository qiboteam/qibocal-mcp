Autocalibration agent
=====================

Choose the one-shot Pydantic AI ``qibocal-agent`` or the reusable Python API
below, or connect any MCP-capable interface (GitHub Copilot CLI, Claude Code,
Cursor, OpenCode, ...) directly to the server and use its ``calibrate`` prompt
or tool. They share calibration policy and deployment settings. The agent
discovers platform context, chooses protocols, inspects their reports and
explicitly accepts or rejects fitted updates. Each integration manages its MCP
subprocess; do not launch a server separately.

Start with the minimal local setup in :doc:`../installation`, then
:doc:`../getting-started`. The tutorials cover local calibration and remote
execution. Alternative providers, persistent settings and deployment
customization are optional; see :doc:`../guides/index` when needed.

.. toctree::
   :maxdepth: 2

   ../installation
   ../getting-started
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

   from qibocal_mcp.agent import open_calibration_agent
   from qibocal_mcp.deployment import DeploymentSettings

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
