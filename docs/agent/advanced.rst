Advanced usage
==============

This page collects the options the tutorials leave out. Everything here is
optional.

Client options
--------------

The tutorial registers the server with ``command`` alone. GitHub Copilot CLI
accepts more options in ``~/.copilot/mcp-config.json``:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "type": "local",
         "command": "/full/path/to/qibocal-mcp-servers/.venv/bin/qibocal-agent",
         "args": ["mcp"],
         "env": { "SSH_AUTH_SOCK": "${SSH_AUTH_SOCK}" },
         "tools": ["*"],
         "timeout": 3600000
       }
     }
   }

* ``tools`` selects which tools Copilot may use; ``["*"]`` enables all of
  them. Add it if Copilot does not list the qibocal tools.
* ``timeout`` is the time Copilot waits for a tool, in milliseconds. A SLURM
  job may wait in the queue, so allow enough time, here one hour.
* ``env`` passes environment variables to the server. Copilot does not hand
  MCP servers your whole environment, so pass ``SSH_AUTH_SOCK`` if your SSH key
  is held by an agent with a passphrase.

Other assistants have equivalent options in their own configuration format.

Settings on the command line
----------------------------

Instead of the configuration file, settings can be given as ``args`` of the
registration, using the flags of :doc:`../reference/cli`:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "command": "/full/path/to/qibocal-mcp-servers/.venv/bin/qibocal-agent",
         "args": ["mcp", "--platform", "qutrits", "--ssh", "mycluster",
                  "--partition", "sim"]
       }
     }
   }

Command-line values override the configuration file, but only for the
configuration selected at startup: switching within a session uses the stored
settings. With ``--no-config``, the file is ignored and switching is disabled.

Where to run what
-----------------

The first tutorial runs the assistant on your computer. It can also run on
the cluster:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - The assistant runs on
     - Register as its command
   * - Your computer, with hardware behind SSH
     - ``qibocal-agent mcp``, with ``ssh`` configured
   * - The SLURM access node
     - ``qibocal-agent mcp`` without ``ssh``, as in
       :doc:`../tutorials/access-node`, or directly
       ``qibocal-mcp --platform NAME --partition QUEUE``
   * - An allocated compute node, or a machine wired to the instruments
     - ``qibocal-mcp --platform NAME``, without partition

The plain ``qibocal-mcp`` server reads no configuration file, so its settings
go in ``args``; see :doc:`../mcp/getting-started`. On the cluster, install the
project in a folder the compute nodes also see, and check that SLURM accepts
your jobs, for example with ``srun --partition QUEUE hostname``. Copilot passes
only a few environment variables to MCP servers, so pass the platforms folder
explicitly with ``--platforms-dir``, and driver variables through ``env``.

Multi-turn sessions from Python
-------------------------------

``open_calibration_agent(settings, model, *, model_base_url=None)`` is an async
context manager yielding a Pydantic AI ``Agent``. Pass a model identifier
or a configured Pydantic AI ``Model`` object; ``model_base_url`` is only valid
with an identifier. ``DeploymentSettings`` contains hardware/deployment options,
not model settings, and does not read the configuration file automatically.

.. code-block:: python

   import asyncio

   from qibocal_agent.agent import open_calibration_agent
   from qibocal_agent.deployment import DeploymentSettings

   async def calibrate():
       settings = DeploymentSettings(platform="YOUR_PLATFORM")
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
All turns must stay inside the context: it keeps the server and its run
handles alive until exit. Retain ``result.all_messages()`` for each
subsequent turn; message history alone cannot restore run handles in a new
context. Entering the context performs discovery, which can connect hardware
or queue a compute job.

Further reading
---------------

* Every configuration setting, managed environments and platform
  repositories: :doc:`../guides/configuration`.
* The cluster deployment in depth, with shared paths and scheduler options:
  :doc:`../guides/cluster`.
* Model providers for the autonomous agent: :doc:`../guides/model-providers`.
