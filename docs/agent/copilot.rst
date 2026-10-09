Interactive calibration with Copilot
====================================

``qibocal-copilot`` opens an interactive GitHub Copilot CLI session with a
generated Qibocal calibration profile. It shares calibration policy and
deployment with the Pydantic AI agent, but Copilot owns conversation history,
login, subscription and model selection. No Pydantic AI extra is required,
and model API calls remain outside the MCP server.

Minimal installation and first session
--------------------------------------

Install GitHub Copilot CLI separately and make ``copilot`` available on ``PATH``.
Use a version supporting ``--plugin-dir``, ``--agent``, interactive objectives,
``--disable-builtin-mcps`` and ``--excluded-tools``.
Authenticate through Copilot under your site's
subscription and data policy. From this checkout:

.. code-block:: sh

   uv sync
   uv run qibocal-copilot --platform YOUR_PLATFORM \
     "Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider
      than 20 MHz, use drive amplitude at most 0.1 and at most 100 shots.
      Run at most two protocols. Inspect plots before accepting an update.
      Stop on errors or insufficient evidence."

The same execution-host prerequisites apply: Python 3.12/3.13, Chrome, a working
Qibolab platform and instrument drivers. Assume ``QIBOLAB_PLATFORMS`` is already
configured; see :doc:`../installation` for advanced deployment. Replace the
target and illustrative bounds with operator-approved values, confirm exclusive
instrument access and read :doc:`../guides/safety`.

An optional positional objective starts Copilot in interactive mode (``-i``),
not a one-shot prompt. Omit it to start a blank session:

.. code-block:: sh

   uv run qibocal-copilot --platform YOUR_PLATFORM

Establish approved bounds before discovery, acquisition or updates. Discovery
is not necessarily hardware-free: a direct server connects its platform, and
queued discovery launches a compute job that connects hardware.

Native options and deployment defaults
--------------------------------------

Store named agents under ``agents.NAME`` in ``~/.config/qibocal/agent.json``.
Then ``qibocal-copilot NAME`` launches with those deployment settings and the
optional saved ``objective`` and ``copilot_options``. A second positional
argument overrides the saved objective; native arguments after ``--`` follow
saved native options. ``qibocal-report-server NAME`` serves the same output
location, inheriting its SSH/working-directory settings and optional nested
``report`` overrides. See :doc:`../guides/configuration`.

Pass native Copilot options after ``--``:

.. code-block:: sh

   uv run qibocal-copilot --platform YOUR_PLATFORM \
     "YOUR_BOUNDED_OBJECTIVE" -- --model MODEL_ID

Use the exact model ID available to your Copilot subscription, not a Pydantic AI
provider prefix. Choose an image- and tool-capable Copilot model. The native
integration stages report PNGs locally for Copilot's ``view`` tool because the
previously tested BYOK OpenAI/Anthropic adapters dropped inline MCP image content.
Pixel delivery is confirmed with **Copilot CLI 1.0.83 and the subscription model
``gpt-5.4-mini``** (2026-10-09): the hardware-free probe used the generated
calibration profile, the real report relay and native ``view``, and correctly
identified all six randomly generated colored shapes. This subscription path
also delivered inline MCP images.

This is not a provider-independent guarantee: the offline BYOK Anthropic adapter
omitted even viewed pixels despite reporting success. Image delivery depends on
the selected model/client; successful staging or a viewer call alone does not
establish that the model received pixels. Reconfirm delivery when changing the
client, model or provider using the probe below. If rendered pixels are absent,
stop and ask the operator to attach the report images to the conversation, or use
the Pydantic AI path. Do not claim visual inspection or accept an update from text
alone.

The wrapper reads a named entry, or the existing ``agent`` section for unnamed
defaults, and accepts
the same hardware/deployment options as ``qibocal-agent``: platform, output,
platform paths, managed environments, repositories, dependencies, SSH, SLURM,
server interpreter, working directory and per-operation timeout.
The timeout defaults to 3600 seconds. Copilot's adapter accepts only 0.001
through 2147483.647 seconds, inclusive, and converts seconds to milliseconds
within Node's integer timeout limit. This constraint is native-Copilot-only;
the timeout still includes queue waiting and is not an entire-session budget.
``agent.model`` and ``agent.model_base_url`` are neither required nor used.
Configure models and login through Copilot instead. There is no new
``copilot`` configuration section; see :doc:`../guides/configuration` and
:doc:`../reference/cli`.

For example, after satisfying the shared-storage and authentication requirements
in :doc:`../tutorials/ssh-slurm`:

.. code-block:: sh

   uv run qibocal-copilot --platform YOUR_PLATFORM \
     --ssh ACCESS_NODE --partition PARTITION \
     --venv /shared/YOUR_USER/qibocal-mcp/env \
     --workdir /shared/calibration-work --output /shared/calibration-runs \
     "YOUR_BOUNDED_OBJECTIVE"

Native options cannot replace the selected profile, plugin directory or
interactive launch with another agent or execution mode, or override tool
availability using ``--available-tools`` or ``--excluded-tools``.
No blanket automatic permission allowance is added.

Generated profile and permissions
---------------------------------

Each normal wrapper launch renders a transient plugin from the package's
shared calibration policy and resolved deployment settings, then launches:

.. code-block:: sh

   copilot --plugin-dir PATH \
     --agent qibocal-calibration:qibocal-calibration \
     --disable-builtin-mcps --excluded-tools=skill,sql

Direct launch uses the qualified plugin/profile ID
``qibocal-calibration:qibocal-calibration``. Copilot's profile picker displays
the short profile name, ``qibocal-calibration``.

The generated profile allowlist names Qibocal's
``get_calibration_context``, ``execute_protocol`` and ``update_platform``,
plus Copilot's ``view`` and ``ask_user`` for normal interactive sessions.
``view`` is a general file/image reader; instructions limit its use to staged
report PNGs, but this is **not an enforced image-only restriction**.
The profile does not pin a model. The discovery tool aliases the
``qibocal://context`` resource for clients without resource support.

The restriction relies on the launch command **as well as** the profile:
``--disable-builtin-mcps`` disables built-in MCP servers and
``--excluded-tools=skill,sql`` excludes Copilot's implicitly injected utilities.
Selecting an exported profile in an already-running session without these
session flags can expose ``skill`` and ``sql`` despite the profile allowlist.
Use the export's printed launch command rather than selecting the profile alone.

The wrapper does not install the plugin, duplicate policy in your repository,
or change global Copilot settings. Copilot's native permission behavior remains
in effect; Pydantic AI retains its existing default behavior. Neither integration
adds a new safety or approval gate. Tool restrictions and model instructions
are not a hardware safety layer; operator-approved bounds, oversight and
site interlocks remain necessary.

Inspecting report images
------------------------

A native-only FastMCP proxy keeps one upstream MCP client connected for the
session, preserving upstream tool schemas, original content and metadata.
Returned PNG bytes arrive over MCP and are staged on the **client host**:

.. code-block:: text

   $XDG_CACHE_HOME/qibocal/agent/copilot-reports/<session>/<call>/report-N.png

When ``XDG_CACHE_HOME`` is unset or empty, the root is
``~/.cache/qibocal/agent/copilot-reports``. The proxy adds
``copilot_report_images`` (a local path list) and ``inspection_required`` to
both text and structured response metadata. Inspect those PNGs using ``view``
before deciding whether evidence supports an update.

This works the same way for local, SSH and SLURM execution: it stages returned
MCP bytes, not remote filesystem downloads, and requires no shell tool access.
Original remote artifact paths remain remote. The Pydantic AI integration
continues to consume original inline MCP images unchanged.

For execution hosts without Chrome, use ``--report-rendering client`` or set
``agent.report_rendering`` to ``client`` before exporting a plugin. The server
then saves HTML and Plotly JSON reports and sends embedded figures over MCP,
including through SLURM. The local Copilot relay renders those figures to PNGs
and stages them at the same client-local paths. Chrome is needed only on the
machine running Copilot; hardware drivers still belong in the execution-host
environment. Rendering failures are tool errors, not successful image-free
reports; do not blindly retry the acquisition after such an error.
The default, ``server``, retains execution-host PNG rendering and remote PNG
artifacts. Client rendering is supported by the native Copilot relay, not the
Pydantic AI agent, and does not guarantee pixel delivery by a BYOK provider.

PNG-containing folders are private (mode ``0700``). Staged artifacts persist for
inspection; there is no automatic cache cleanup. Delete selected session
directories after use according to your site's retention policy.
Copilot's native path permissions can still prompt for ``view`` access; the
wrapper adds neither ``--add-dir`` nor blanket permissions.
The proxy marks inspection as required but does not enforce evidence checks or
approval. Stop if an image-capable model or usable viewer is unavailable.

Hardware-free subscription vision probe
---------------------------------------

From a checkout, explicitly opt in to a small subscription-model probe:

.. code-block:: sh

   QIBOCAL_COPILOT_VISION_MODEL=gpt-5.4-mini \
     uv run python -m unittest discover -s tests -p test_copilot_vision.py -v

The probe is skipped during ordinary test runs. It uses your existing Copilot
login and removes BYOK provider environment overrides from the child process.
It replaces the hardware server with a synthetic MCP fixture while retaining
the generated calibration policy, tool restrictions and production report relay.
Only the synthetic tools and ``view`` are preapproved; no blanket permission
allowance is added.

The fixture generates a random six-cell PNG and never sends the expected colors
or shapes as text or image metadata. The test relay removes inline image copies
after production staging, so inline delivery cannot mask a broken viewer.
Success requires one discovery, one synthetic protocol, a successful ``view``
of the staged PNG and an exact match for all six cells, with no platform update.
Temporary plugins, images and answer files are removed afterwards. This confirms
visual transport, not real-hardware calibration accuracy.

The probe consumes subscription usage. It has a 180-second subprocess timeout
and a native 30-AI-credit session ceiling, the minimum accepted by the tested CLI;
the ceiling is not a usage estimate. Use an image- and tool-capable subscription
model available to your account.

Persistent plugin export
------------------------

To use Copilot's native command with the current named settings:

.. code-block:: sh

   copilot $(qibocal-copilot myremote --print-options)

``--print-options`` generates a persistent plugin and prints the complete
shell-quoted options, including the profile, session tool restrictions, saved
objective and native options. No Copilot binary, login or hardware is needed.
The simple substitution above is suitable only for shells that split command
output, such as Bash, and argument vectors with no whitespace or shell
metacharacters: command substitution does not interpret shell quotes.
For arbitrary paths and objectives, use NUL-delimited arguments in Bash or Zsh:

.. code-block:: bash

   copilot_args=()
   while IFS= read -r -d '' arg; do copilot_args+=("$arg"); done \
     < <(qibocal-copilot myremote --print-options0)
   (( ${#copilot_args[@]} )) && copilot "${copilot_args[@]}"

If generating options fails, do not launch Copilot with an empty options array.
Generated plugins live under ``$XDG_DATA_HOME/qibocal/agent/plugins`` (default
``~/.local/share/qibocal/agent/plugins``). Each snapshot is immutable and
content-addressed: identical settings reuse the same path, changes produce a
new one, and manually modified files are rejected rather than overwritten.
Only environment names are stored, never their values. Old snapshots remain
available for active sessions and can be removed explicitly after use.

Export a plugin to a **new** directory without starting a session:

.. code-block:: sh

   uv run qibocal-copilot --platform YOUR_PLATFORM \
     --export-plugin ./qibocal-plugin
   copilot --plugin-dir ./qibocal-plugin \
     --agent qibocal-calibration:qibocal-calibration \
     --disable-builtin-mcps --excluded-tools=skill,sql

Export refuses an existing target. It requires no Copilot binary, login or
hardware connection, and prints a launch command; it does not install anything.
The printed command is authoritative and includes the required session-level
tool exclusions.
To use a local executable wrapper such as ``copilot-qrc`` for BYOK environment
settings, replace only ``copilot`` in that printed command with the wrapper's
name. Preserve every profile and permission flag. The normal direct launcher
still looks up ``copilot`` on ``PATH``.
Do not combine export with an explicit objective or native Copilot arguments.
Saved named-agent objectives and native options appear in the printed command.
Standard
deployment/configuration validation still applies.

The exported plugin snapshots the policy and resolved deployment/defaults.
The wrapper records environment variable **names** from the rendering
environment, excluding model-provider credential/configuration prefixes and
GitHub authentication tokens, but preserving ``COPILOT_MCP_*`` driver variables.
Plugin entries use runtime ``${NAME}`` references, not secret values.
Those recorded names must
still be defined in the later launch environment; the server relay reports a
clear error if a required name is missing. Referenced values are passed
literally, including values containing ``${literal}``.
Export does not create a credential store or a portable copy of your environment.
Required driver keys remain runtime references; their values are not copied
into generated files.
Keep credentials out of objectives, paths, repository URLs and dependency
arguments, which become part of the deployment snapshot.

The exporting installation's interpreter path and package must remain available.
Re-export to a new directory when deployment, defaults, policy or the set of
environment variable names to forward changes. Values of already recorded names
are resolved at launch, not frozen at export. Review generated files before
sharing them; they can contain platform names, deployment paths and source
metadata.

Session lifetime and resume
---------------------------

The native wrapper regenerates its plugin on every launch. A transient plugin
path is not stable session-resume configuration; use a persistent export when
you need a stable plugin path for Copilot's native session management.

Resuming a Copilot conversation does **not** preserve the old direct server or
gateway, run handles or accepted-state handoff. A restarted MCP session has
fresh handles, even if saved report files and conversation history still exist.
Do not try to update an old run in the new session. See
:doc:`../guides/artifacts` for retained evidence and
:doc:`../guides/architecture` for execution-state boundaries.
