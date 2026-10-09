Agent installation
==================

Minimal installation
--------------------

For the Pydantic AI ``qibocal-agent`` CLI and Python API, follow the setup below.
For interactive GitHub Copilot CLI, see :doc:`agent/copilot`: ``uv sync`` and an
installed Copilot CLI suffice, without the ``agent`` extra or Python model
credentials. Execution-host hardware prerequisites are shared.

For a standard local setup, you need ``uv``, Python 3.12 or 3.13, Chrome,
and a working Qibolab platform with its hardware drivers. Assume your site
has already set ``QIBOLAB_PLATFORMS`` to the platform definitions.
From a checkout of this repository:

.. code-block:: sh

   uv sync --extra agent
   export OPENAI_API_KEY=...

Skip the export if your OpenAI credentials are already configured.
``uv`` installs Qibocal and all required Python dependencies, including the
agent's OpenAI provider. There is no separate MCP installation or server
launch step. Continue directly to :doc:`getting-started`.

Only use the sections below if the standard setup does not match your site
or you want to customize it.

Advanced installation and customization
---------------------------------------

Missing prerequisites and alternative installation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If your platform paths or credentials are not already configured:

.. code-block:: sh

   export QIBOLAB_PLATFORMS=/absolute/path/to/your/platforms
   export OPENAI_API_KEY=...

For another provider, use its recommended environment or credential settings.
The same Pydantic AI extra supports GitHub Copilot subscriptions and OpenWebUI's
OpenAI-compatible API; see :doc:`guides/model-providers` for authentication
and launch commands. This provider integration is distinct from the interactive
Copilot CLI wrapper. Install other Pydantic AI provider extras yourself if
you select a provider not included in the agent extra. Do not put API keys
in command objectives or in ``qibocal-mcp.json``.

If Chrome is missing, install it for Kaleido's Plotly image export:

.. code-block:: sh

   uv run plotly_get_chrome -y

Alternatively, ``devenv shell`` configures Python 3.12 and syncs all extras,
following Qibolab's Python/uv setup. It also installs Chrome through Nix and
sets ``BROWSER_PATH`` for Kaleido, so no separate ``plotly_get_chrome`` step is
needed in that environment. The documentation extra and build commands are
in :doc:`development`. Only the machine running the Pydantic AI agent needs
its extra and model credentials; the native Copilot integration uses Copilot's
login and model selection. SSH server deployment is handled automatically.

Platform selection and repositories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A platform name is required at runtime. Qibocal's test-only ``mock`` platform
is not included in its installed package; it is not a ready-to-run hardware
substitute. Use the qubit IDs discovered from your own platform.

``--platforms`` can supply ``QIBOLAB_PLATFORMS`` on the execution host.
Repository roots supplied by repeatable ``--platform-repo`` are added after
explicit ``--platforms`` paths. Without explicit paths, the execution host's
inherited ``QIBOLAB_PLATFORMS`` follows those roots. With neither option, its
environment value is left untouched. See :doc:`guides/configuration` for
repository formats, dependency discovery and persistent defaults.

Managed execution environments
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An SSH agent launch without ``--server-python`` provisions a managed environment
named ``qibocal-mcp`` by default. Use ``--venv NAME_OR_PATH`` to choose another
environment. Local agent launches manage an environment only when ``--venv``
is supplied; otherwise they use an existing interpreter.

Named environments live on the execution host under
``$XDG_CACHE_HOME/qibocal-mcp/envs/NAME`` or, when unset/empty,
``~/.cache/qibocal-mcp/envs/NAME``. Absolute paths, ``./`` and ``../`` paths are
resolved on that host; ``--venv`` also expands ``~`` there, including remotely.
These environments are separate from Qibocal notebook environments:
``qibocal/envs`` is not used or modified.
Managed deployment requires POSIX. An existing environment must contain
``pyvenv.cfg`` and executable ``bin/python``; conda/system interpreters should
be selected with the agent's ``--server-python`` instead.

For SSH provisioning, the remote host needs ``python3`` version 3.12 or 3.13,
``git``, and an installer/bootstrap route: Python's ``pip``/``ensurepip``,
``uv``, or external ``pip>=22.3``. A standard-library bootstrap transfers the
launching installation's server package source and package metadata over SSH,
using the checkout when available or installed distribution metadata otherwise,
not user configuration or model keys. It installs the server dependencies and
pinned Qibocal in the environment. No remote checkout, preinstalled ``qibocal-mcp``
or agent extra is required. A wheel-installed agent is supported: the bootstrap
reconstructs minimal installation metadata from the distribution's declared
requirements, retaining the Qibocal pin. Source snapshots and repository clones
are cached on the execution host; provisioning messages go to stderr, preserving MCP
stdout. The Pydantic AI agent resolves its model provider before deployment or
hardware access; native Copilot manages its own model and authentication.

Environments are reused. Successful dependency state is fingerprinted, including
nested local ``-r``/``-c`` files, root packaging metadata and repository Git
``HEAD``; source, dependency or requirements changes reconcile installation.
All package metadata, dependency specifications and requirements are installed
in one resolver request. Managed environments run an installer dependency check
and import the server before launch. Installation is additive, not an exact
sync: removing a configured dependency does not
uninstall it. Errors stop startup rather than silently continuing. Existing
broken or custom environments are never automatically deleted or recreated.
The Qibocal pin from the source ``pyproject.toml`` or installed distribution
metadata is honored in managed environments; incompatible dependencies produce
installer resolver errors.
Its installed Git commit is verified, including when another source reports
the same package version. Package-state changes and environment-expanded local
requirements also trigger reconciliation. Local project references and includes
are rebased relative to their original requirements file in cached copies;
see :doc:`guides/configuration` for the complete dependency-selection rules.

``--server-python`` selects an externally managed interpreter that must already
contain the server. It bypasses automatic environment creation and core-server
installation and cannot coexist with ``--venv``. Repository/dependency
options without a managed environment install into the selected existing
interpreter, intentionally modifying it. Prefer ``--venv`` for isolation.
The agent uses the server interpreter for queued workers as well.

Chrome and system-level instrument drivers remain site-managed; managed
environments do not install them. Python driver packages can be supplied with
repeatable ``--dependency REQUIREMENT``. Configure only trusted repositories
and dependencies: package metadata and requirements may execute code.

Pinned Qibocal API
~~~~~~~~~~~~~~~~~~

Qibocal is pinned to commit
``87abd3350612b72e64b09db115ee0cf22886b507`` of
`qiboteam/qibocal#1716 <https://github.com/qiboteam/qibocal/pull/1716>`_.
The pin is a PEP 508 direct Git requirement in ``project.dependencies``, not a
uv-only source override, so pip and built distributions honor the same commit.
This project uses that commit's API, not the PR description's older examples.
Protocol execution calls ``Executor.__call__()`` with automatic fitted updates
disabled; accepted updates call ``Executor.update(completed)`` explicitly.
Changing the pin requires checking those APIs and the saved artifact format.

Cluster installation
~~~~~~~~~~~~~~~~~~~~

Provision a managed environment on shared storage, or install the server in an
externally managed shared interpreter (``uv sync`` suffices for a checkout).
An SSH agent provisions the managed environment automatically; see
:doc:`tutorials/ssh-slurm` for the launch command and site prerequisites.
The Python environment, working directory, cached source snapshots and
repository clones, platform definitions and output directory must be available
at identical absolute paths on access and compute nodes. Ensure the execution
host's cache location is also shared; selecting a shared ``--venv`` alone is
not sufficient if its cache remains node-local. There is no automatic
node-local staging or compute-node provisioning.
Chrome and instrument drivers must be available on compute nodes, where
reports and acquisitions actually run.
