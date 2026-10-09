Networking and execution hosts
==============================

Stdio, not HTTP
---------------

An MCP client launches ``qibocal-mcp`` as a stdio subprocess.
The agent either launches the Python server locally or runs it via SSH.
MCP messages, including report images, travel over that stream.
No HTTP listener, port forwarding or HTTP tunnel is needed.

Stdout belongs to the protocol. Hardware-driver and Qibocal output is redirected
to stderr by the server; remote startup scripts must not print banners to
stdout and corrupt the stream.

Model-provider and instrument traffic
-------------------------------------

The absence of an MCP HTTP service does not mean the entire workflow is
offline. The agent communicates with the selected model provider from the
machine running the agent. For example, an OpenAI model uses the provider's
HTTPS API; other providers or locally hosted models have their own connection
requirements. Objectives, platform context, tool results and report images
may be sent to that model. Choose a provider and network/data policy approved
for your experiment data. The MCP server and SLURM workers do not make model
API calls.

Instrument communication is separate again: Qibolab drivers use the addresses
and connection mechanisms configured by your platform. In direct mode the
server host must reach the instruments; in queued mode the compute nodes must.
SSH carries MCP messages to the gateway, not instrument connections or a
general-purpose network tunnel.

Agent locations
---------------

.. list-table:: Where the agent runs
   :header-rows: 1
   :widths: 24 46 30

   * - Agent location
     - Execution path
     - Options
   * - Laptop/local machine
     - SSH to access node, then one ``srun`` job per operation
     - ``--ssh ACCESS_NODE --partition PARTITION``
   * - Access node
     - One ``srun`` job per operation
     - ``--partition PARTITION``
   * - Allocated compute node
     - Direct execution, one persistent hardware connection
     - Neither option

SSH authentication and environment
----------------------------------

Use your SSH configuration for host aliases, authentication and any
``ProxyJump``. SSH is launched with ``-T`` and ``BatchMode=yes``:
there is no interactive password prompt. Configure keys or an SSH agent first.
The local SSH authentication socket is available to the SSH process.

Locally, the agent's server inherits its environment, preserving
``QIBOLAB_PLATFORMS`` and driver settings. Across SSH, the laptop's environment
and model credentials are not forwarded to the remote server. Set remote
driver settings in the access node's noninteractive environment.
``--platforms`` explicitly sets remote ``QIBOLAB_PLATFORMS``.
The gateway exports its environment to SLURM workers (``--export=ALL``).

Keep model credentials on the machine running the agent. There is no reason to
put them on compute nodes for server-only execution. If you run the agent on
an access node, review what environment variables are exported to jobs under
your site's credential policy.

Shared paths and interpreters
-----------------------------

For queued mode, the server Python environment, working directory, platform
definitions and output directory must be available at identical paths on
access and compute nodes. Use absolute shared paths. Chrome and instrument
drivers must exist on compute nodes as well.

The agent's ``--server-python`` selects the server interpreter: the current
Python interpreter locally, or ``python3`` with SSH if unset. Its queued
workers use that interpreter by default. A standalone gateway can select
another worker interpreter with ``--worker-python``.

``--workdir`` on the agent sets the server launch directory (remotely for SSH).
On the standalone server it sets the SLURM worker's working directory.
Relative paths are interpreted against the execution host's current directory,
not the configuration file's location. They are not expanded or rebased.

SLURM options and transport integrity
-------------------------------------

The gateway dispatches one node and one task per operation with ``srun``.
Additional scheduling options can be supplied repeatedly, using:

.. code-block:: sh

   --slurm-arg=--account=ACCOUNT --slurm-arg=--time=00:15:00

Useful site-specific requests include account, time, constraint, reservation
and hardware resources. A nonempty ``slurm_arg`` list requires a partition.
Do not use extra arguments to redirect stdin/stdout/stderr, alter task count
or detach the job: the gateway needs the worker's request/response stream and
controlled lifecycle.

The agent timeout includes queue waiting and execution for each operation.
Standalone MCP clients must configure their own timeout. Queue and subprocess
failures are surfaced as errors, not successful experiments.
For cancellation behavior, see :doc:`safety`; for a complete launch command,
see :doc:`../tutorials/ssh-slurm`.
