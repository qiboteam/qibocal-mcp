SSH and SLURM: calibrate without holding an idle allocation
===========================================================

Goal and prerequisites
----------------------

Keep the agent and model credentials on your laptop while a hardware-free
gateway on an access node dispatches one SLURM job per operation.
This follows Qibocal's
`interactive infrastructure <https://qibo.science/qibocal/stable/tutorials/interactive.html>`_.

First complete :doc:`../installation` on the appropriate hosts and confirm:

* Noninteractive SSH authentication works through your SSH configuration,
  including any ``ProxyJump``. Configure keys or an SSH agent beforehand.
* The Python environment, working directory, platforms and output storage
  are accessible at identical absolute paths on access and compute nodes.
* Compute nodes can reach the instruments, and have Chrome and drivers.
* Your partition, account, resource requests and hardware limits are approved.

.. figure:: ../_images/queued-workflow.svg
   :alt: A laptop agent sends MCP messages and report images through SSH stdio to a hardware-free access-node gateway. The gateway runs discovery, acquisition and explicit update as separate compute jobs. An accepted snapshot on shared storage feeds later jobs; rejected fits do not change that snapshot.
   :width: 100%

   Original topology and state-handoff diagram. No HTTP listener or tunnel is
   involved, and no compute allocation is held while the model inspects reports.

1. Check access without touching hardware
-----------------------------------------

Replace the placeholders and run:

.. code-block:: sh

   ssh -T -o BatchMode=yes ACCESS_NODE \
     'cd /shared/qibocal-mcp-servers && .venv/bin/python -m qibocal_mcp.server --help'

This checks login, the working directory and server importability. It does not
prove compute-node path availability, instrument access or Chrome availability;
verify those through your site's normal allocation procedure.

Set driver environment variables on the access node using your site's supported
noninteractive setup. ``--platforms`` explicitly supplies remote
``QIBOLAB_PLATFORMS``; other driver settings are exported from the gateway
environment to SLURM. The laptop's environment and model credentials are
not forwarded over SSH. See :doc:`../guides/networking`.

2. Launch the laptop agent
--------------------------

.. code-block:: sh

   export OPENAI_API_KEY=...
   uv run qibocal-agent --no-config \
     --model openai:gpt-4.1 --platform YOUR_PLATFORM \
     --ssh ACCESS_NODE --partition PARTITION \
     --server-python /shared/qibocal-mcp-servers/.venv/bin/python \
     --workdir /shared/qibocal-mcp-servers \
     --platforms /shared/qibolab-platforms \
     --output /shared/calibration-runs \
     --slurm-arg=--account=YOUR_ACCOUNT --slurm-arg=--time=00:15:00 \
     --timeout 7200 \
     "Calibrate qubit 0's drive frequency using spectroscopy.
      Sweep no wider than 20 MHz, use drive amplitude at most 0.1
      and at most 100 shots, if these are approved for this platform.
      Run at most two protocols. Inspect plots and fit uncertainty
      before accepting an update. Stop on errors or insufficient evidence.
      Summarize run IDs, updates, remaining uncertainty and cluster paths."

Replace the target and limits before executing; conditional wording is not a
safety check. ``--no-config`` makes this example independent of stored defaults.
``--timeout`` is a per-operation limit including queue wait, not a limit on the
entire objective. ``--slurm-arg=--time=00:15:00`` sets a scheduler limit rather
than replacing the MCP timeout.

The first context read submits a discovery job. Each protocol then gets a fresh
job that connects, acquires, fits, reports, disconnects and releases its
allocation. An accepted update is another job that restores the saved execution,
applies its results and saves a snapshot. Later jobs use the latest accepted
Qibolab parameters *and* Qibocal calibration, not the most recent rejected fit.
See :doc:`../guides/architecture` for the lifecycle.

3. Optional persistent cluster defaults
---------------------------------------

Store reviewed agent settings rather than repeating paths every time:

.. code-block:: sh

   uv run qibocal-mcp-config set agent.platform YOUR_PLATFORM
   uv run qibocal-mcp-config set agent.model openai:gpt-4.1
   uv run qibocal-mcp-config set agent.ssh ACCESS_NODE
   uv run qibocal-mcp-config set agent.partition PARTITION
   uv run qibocal-mcp-config set agent.server_python /shared/qibocal-mcp-servers/.venv/bin/python
   uv run qibocal-mcp-config set agent.workdir /shared/qibocal-mcp-servers
   uv run qibocal-mcp-config set agent.platforms /shared/qibolab-platforms
   uv run qibocal-mcp-config set agent.output /shared/calibration-runs
   uv run qibocal-mcp-config set agent.timeout 7200
   uv run qibocal-mcp-config set agent.slurm_arg '["--account=quantum", "--time=00:15:00"]'
   uv run qibocal-mcp-config show

Use your actual account, not the illustrative ``quantum`` value. After reviewing
the file, a normal ``uv run qibocal-agent "YOUR_BOUNDED_OBJECTIVE"`` uses those
defaults. Explicit CLI settings override the agent section. The agent passes
resolved settings and ``--no-config`` to its server, so configuration stored on
the access node cannot silently inject another platform or partition.

4. Inspect results and retain cluster artifacts
-----------------------------------------------

Images and results return over MCP and can be inspected on the laptop.
Artifacts remain under
``OUTPUT/sessions/<session_id>/<run_id>/`` on the cluster. Copying the remaining
files is a separate operation, for example:

.. code-block:: sh

   scp -r ACCESS_NODE:/shared/calibration-runs/sessions/SESSION_ID/RUN_ID ./retained-run

Use the actual IDs and follow your site's data handling policy. Do not retry
failed acquisitions or partially applied updates blindly. Timeout/cancellation
and allocation release depend on SLURM/site signal handling; see
:doc:`../guides/safety`.

Other agent locations and clients
---------------------------------

**Agent on the access node:** use the launch command without ``--ssh``.
Omit ``--server-python`` if the agent and workers use the same shared Python
environment. Install the agent extra and provide credentials on that node.

**Agent on an allocated compute node:** first acquire an allocation through
your site's procedure, then omit ``--ssh``, ``--partition`` and ``--slurm-arg``.
This is direct mode with one persistent hardware connection and no nested jobs.
Use ``--no-config`` or a reviewed configuration file so stored queue settings
do not accidentally re-enable dispatch.

**Other MCP clients:** launch the queued gateway directly:

.. code-block:: sh

   uv run qibocal-mcp --no-config --platform YOUR_PLATFORM \
     --partition PARTITION --platforms /shared/qibolab-platforms \
     --output /shared/calibration-runs

Run this on the access node, with the shared repository as the working directory.
Use ``--worker-python`` if workers need a different shared interpreter. Your
client must set its own operation timeout; the standalone server has no
``--timeout`` option.
