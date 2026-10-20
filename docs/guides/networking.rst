Networking and execution hosts
==============================

Stdio, not HTTP
---------------

An MCP client launches ``qibocal-mcp`` (or ``qibocal-mcp-remote``) as a stdio
subprocess. The orchestration either launches the Python server locally or
runs it via SSH. MCP messages, including report images, travel over that
stream. No HTTP listener, port forwarding or HTTP tunnel is needed.

Stdout belongs to the protocol. Hardware-driver and Qibocal output is redirected
to stderr by the server, as are managed-environment provisioning logs; remote
startup scripts must not print banners to stdout and corrupt the stream.

Model-provider and instrument traffic
-------------------------------------

The absence of an MCP HTTP service does not mean the entire workflow is
offline. The agent communicates with the selected model provider from the
machine running the agent. For example, an OpenAI model uses the provider's
HTTPS API; other providers or locally hosted models have their own connection
requirements. Objectives, platform context, tool results and report images
may be sent to that model. Choose a provider and network/data policy approved
for your experiment data. The MCP server and SLURM workers do not make model
API calls; interactive clients own their model connection and login.
The Pydantic AI agent resolves its model provider before deploying the server
or accessing hardware. Deployment does not transfer model keys or user
configuration.

Provisioning may also need network access from the execution host to Git sources
and Python package indexes. Repository authentication is separate from access
node login. Trust and review these sources: package metadata and requirements
can execute code.

Instrument communication is separate again: Qibolab drivers use the addresses
and connection mechanisms configured by your platform. In direct mode the
server host must reach the instruments; in queued mode the compute nodes must.
SSH carries MCP messages to the gateway, not instrument connections or a
general-purpose network tunnel.

Client locations
----------------

.. list-table:: Where the agent or interactive client runs
   :header-rows: 1
   :widths: 24 46 30

   * - Client location
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

The same options apply to ``qibocal-agent`` and ``qibocal-mcp-remote``; on the
access or compute node the plain ``qibocal-mcp`` server can also be used
directly, without the orchestration package.

SSH authentication and environment
----------------------------------

Use your SSH configuration for host aliases, authentication and any
``ProxyJump``. SSH is launched with ``-T`` and ``BatchMode=yes``:
there is no interactive password prompt. Configure keys or an SSH agent first.
The local SSH authentication socket is available to the SSH process.
That does not automatically forward the laptop's ``SSH_AUTH_SOCK`` to remote
Git commands. Configure clone authentication on the deployment host independently
under your site's policy; never embed credentials in URLs or configuration.

Locally, the agent's server inherits its environment, preserving
``QIBOLAB_PLATFORMS`` and driver settings. Across SSH, the laptop's environment
and model credentials are not forwarded to the remote server. Set remote
driver settings in the access node's noninteractive environment.
``--platforms-dir`` explicitly supplies the first remote ``QIBOLAB_PLATFORMS``
path-list entries, followed by managed ``--platform-repo`` roots in configured
order. Without explicit ``--platforms-dir``, remote inherited
``QIBOLAB_PLATFORMS`` entries follow those roots. With neither option, the
variable is left untouched. The laptop's value is not used as a remote fallback.
The gateway exports its environment to SLURM workers (``--export=ALL``).

The remote noninteractive environment also supplies ``PATH`` for bootstrap tools,
``HOME`` and ``XDG_CACHE_HOME`` for managed caches, and site-specific browser and
driver settings. Source snapshots and clones are cached under
``$XDG_CACHE_HOME/qibocal/agent`` (or ``~/.cache/qibocal/agent``). Named environments
use its ``envs/NAME`` directory; a path supplied through ``--venv`` overrides only
the environment location, not the cache root.

Keep model credentials on the machine running the agent. There is no reason to
put them on compute nodes for server-only execution. If you run the agent on
an access node, review what environment variables are exported to jobs under
your site's credential policy.

Shared paths and interpreters
-----------------------------

For queued mode, the server Python environment, working directory, cached source
snapshots and repository clones, platform definitions and output directory must
be available at identical absolute paths on access and compute nodes.
Use shared storage for both ``--venv`` and the execution host's cache root.
There is no automatic node-local staging or compute-node provisioning. Chrome
and system instrument drivers must exist on compute nodes as well; Python driver
packages can be installed with ``--dependency``.

An unnamed SSH agent without explicit ``--server-python`` provisions and reuses a
managed environment, default name ``qibocal-mcp``; ``--venv NAME_OR_PATH``
selects another. The remote bootstrap needs ``python3`` 3.12/3.13, Git and
``pip``/``ensurepip``, ``uv`` or external ``pip>=22.3``. It transfers this
launching installation's server source and metadata over SSH, using checkout
source or installed distribution metadata, and installs server dependencies
and pinned Qibocal; no remote checkout or agent extra is needed. Managed
environments check installed dependencies and server importability before launch.
Provisioning commands cannot consume MCP stdin and do not prompt for Git/pip
credentials; arrange noninteractive authentication beforehand.
Managed environments are POSIX-only. Provisioning locks protect installation,
not active server sessions: use separate environments for concurrent deployments
with differing source or dependency settings.

Locally, an unnamed launch without ``venv`` or ``server_python`` uses its
current interpreter. Named agents default to a managed environment with their
name, locally and remotely. Explicit ``--server-python`` bypasses automatic
environment/core-server installation and must already contain the server;
it cannot coexist with ``--venv``. Repository/dependency options in an
externally managed environment intentionally install into the selected
interpreter. Prefer ``--venv`` for isolation.

Queued workers use the server interpreter. The standalone ``qibocal-mcp``
gateway performs no provisioning itself; it can select another worker
interpreter with ``--worker-python``, which must already contain the server.

``--workdir`` on the orchestration commands sets the server launch directory
(remotely for SSH). On the standalone server it sets the SLURM worker's
working directory.
Configured relative ``output`` and ``workdir`` paths use the execution host's
``qibocal/agent/runs`` and ``qibocal/agent/work`` cache folders. Relative ``venv``
paths use ``qibocal/agent/envs``. Named agents default to their name in each
folder. All three configured directories expand ``~`` on the execution host.
Explicit CLI output/workdir overrides retain current-directory semantics.
Local repository source
paths also refer to the deployment host. See :doc:`configuration` for repository
fragments, clone reuse without automatic updates and dependency reconciliation.

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
