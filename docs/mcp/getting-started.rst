Getting started with MCP
========================

Complete :doc:`installation`, confirm exclusive instrument access and read
:doc:`../guides/safety`. Your MCP client owns the server subprocess and decides
which tool calls to make.

Launch a stdio server
---------------------

On the machine that reaches the instruments:

.. code-block:: sh

   qibocal-mcp --platform YOUR_PLATFORM --output runs

On a SLURM access node, dispatch every operation through the queue instead:

.. code-block:: sh

   qibocal-mcp --platform YOUR_PLATFORM --partition PARTITION \
     --output /shared/calibration-runs --workdir /shared/calibration-work

Configure your MCP client to launch the chosen command as its stdio
subprocess. The process communicates over stdio; it does not provide an HTTP
URL, and running it in a terminal alone does not initiate a calibration.
For example, a generic MCP client configuration entry looks like:

.. code-block:: json

   {
     "mcpServers": {
       "qibocal": {
         "command": "qibocal-mcp",
         "args": ["--platform", "YOUR_PLATFORM", "--output", "runs"]
       }
     }
   }

To launch the server from your own machine when the hardware is behind SSH,
configure ``qibocal-mcp-remote`` from the orchestration package as the
subprocess instead; see :doc:`../tutorials/interactive`.

The server accepts the MCP connection and lists its tools, prompt and resource
while Qibocal and the direct-mode hardware initialize in the background. The
first context or calibration request waits for that initialization and reports
any failure. In queued mode, set your client's per-operation timeout to cover
queue waits; the server has no ``--timeout`` of its own.

Start from the calibration prompt
---------------------------------

The server distributes its calibration policy as the MCP prompt
``calibrate``. In interfaces that expose prompts (as slash commands or
templates), select it to load the complete workflow guidance: plan from
discovered context, execute one protocol at a time, inspect report images,
record findings as notes, and update the platform only on inspected evidence.

Interfaces that ignore MCP prompts can call the identically named
``calibrate`` **tool**, which returns the same text. Its description invites
the model to call it before any calibration, so in practice asking your
assistant to "calibrate using the qibocal tools" is enough to load the policy.

Make a first tool call
----------------------

Read ``qibocal://context`` first, or call ``get_calibration_context`` with no
arguments if your client does not support resources. Both discover qubit IDs,
protocol definitions, units and update support. Direct-mode startup connects
hardware, and queued discovery launches a compute job that connects it;
discovery is not a hardware-free preflight.

For example, after checking that target ``0`` exists and that these parameters
are approved for your hardware, call ``execute_protocol`` with:

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
update. See :doc:`../reference/mcp` for target arrays and response details.
Use ``append_note`` with the returned ``run_id`` to retain inspected findings
and later follow-ups, or omit ``run_id`` for session plans and summaries.
``get_notes`` reads the corresponding history.

Keep the run's artifact location. Run handles expire when the server or
gateway exits or ``start_session`` switches calibration sessions, even though
the files remain on disk. See :doc:`../guides/artifacts` for output layout and
accepted snapshots.
