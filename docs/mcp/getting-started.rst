Getting started with MCP
========================

Complete :doc:`installation`, confirm exclusive instrument access and read
:doc:`../guides/safety`. Your MCP client owns the server subprocess and decides
which tool calls to make.

Launch a stdio server
---------------------

.. code-block:: sh

   uv run qibocal-mcp --platform YOUR_PLATFORM --output runs

Configure your MCP client to launch that command with this repository as its
working directory. The process communicates over stdio; it does not provide an
HTTP URL. Running it in a terminal alone does not initiate a calibration.
There are three tools: ``get_calibration_context``, ``execute_protocol`` and
``update_platform``, plus the ``qibocal://context`` resource.

The server accepts the MCP connection and lists its tools/resources while
Qibocal and the direct-mode hardware initialize in the background. The first
context or calibration request waits for that initialization and reports any
failure. Report folders are created only when needed by a run.
Managed-environment installation and SSH setup still happen before connection;
queued discovery can additionally wait for a SLURM allocation.

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

Keep the run's artifact location. Run handles expire when the server or
gateway exits, even though the files remain on disk. See
:doc:`../guides/artifacts` for output layout and accepted snapshots.

Advanced: queued gateway over SSH
---------------------------------

Once the shared paths and site prerequisites in :doc:`installation` are
ready, launch a gateway on the access node:

.. code-block:: sh

   uv run qibocal-mcp --no-config --platform YOUR_PLATFORM \
     --partition PARTITION --venv /shared/YOUR_USER/qibocal-mcp/env \
     --platform-repo 'https://github.com/YOUR_ORG/platforms.git#ref=COMMIT&subdirectory=platforms' \
     --output /shared/calibration-runs

Replace placeholders with approved paths and a trusted platform repository.
Run this from the shared working directory on the access node, where the
standalone server itself is installed; configure its source/clone cache on
shared storage. It manages an environment only because ``--venv`` is supplied.
For a client on another machine, configure a stdio subprocess using
noninteractive SSH, for example:

.. code-block:: sh

   ssh -T -o BatchMode=yes ACCESS_NODE \
     'cd /shared/calibration-work && /shared/qibocal-agent/.venv/bin/qibocal-mcp --no-config --platform YOUR_PLATFORM --platforms /shared/qibolab-platforms --partition PARTITION --output /shared/calibration-runs'

This second example uses an externally managed shared installation, which
must already contain the server and drivers. Set your client's operation
timeout to cover queue waits; the standalone server has no ``--timeout``
option. Each operation gets its own allocation, and no allocation is held
while the client inspects results. Read :doc:`../guides/networking` for
SSH environment handling and :doc:`../guides/safety` before retrying a failed
acquisition or update.
