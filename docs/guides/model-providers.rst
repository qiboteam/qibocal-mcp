Model providers
===============

This page configures the **Pydantic AI** ``qibocal-agent`` and reusable Python
API, not the native Copilot CLI integration. For ``qibocal-copilot``, install
only the core package and Copilot CLI, authenticate through Copilot and pass
native model options after ``--``, for example ``-- --model MODEL_ID``.
The generated profile has no fixed model and ignores ``agent.model`` and
``agent.model_base_url``. See :doc:`../agent/copilot`.

Install the ``agent`` extra for all providers below:

.. code-block:: sh

   uv sync --extra agent

Choose a model with **vision and tool calling**. The agent sends objectives,
platform context, fitting tables and report images to the selected provider.
Use an endpoint and data policy approved for your experiment; credentials
belong in the environment, never objectives or ``qibocal-mcp.json``.
These options configure the model connection, not the MCP server's transport.
Local and SSH/SLURM hardware execution work with either provider.

GitHub Copilot subscription via Pydantic AI
-------------------------------------------

The project uses Pydantic AI's native ``GitHubCopilotModel`` and
``GitHubCopilotProvider``, not GitHub Models or a third-party proxy. Supply
a bearer token accepted by the Copilot inference API and an eligible Copilot
subscription. For an existing GitHub CLI OAuth login:

.. code-block:: sh

   # Run gh auth login first if not already authenticated.
   export GITHUB_COPILOT_API_KEY="$(gh auth token)"
   uv run qibocal-agent --model github-copilot:claude-haiku-4.5 \
     --platform YOUR_PLATFORM "YOUR_BOUNDED_OBJECTIVE"

Alternatively, set ``GITHUB_COPILOT_API_KEY`` to an existing supported OAuth
user token or Copilot API token. The provider also reads
``GITHUB_COPILOT_API_TOKEN`` and ``COPILOT_GITHUB_TOKEN`` as fallbacks.
It intentionally does not read ``GITHUB_TOKEN`` or ``GH_TOKEN``.
Classic personal access tokens are not supported; fine-grained PAT support
depends on the subscription and API. This project does not extract tokens
from another application's credential files or persist or renew them.

The example model is illustrative: availability depends on your subscription
and organization policies. Requests consume your subscription's model usage
allowance; authentication alone does not establish entitlement.
List your available models with:

.. code-block:: sh

   curl -H "Authorization: Bearer $GITHUB_COPILOT_API_KEY" \
     -H "Copilot-Integration-Id: vscode-chat" \
     https://api.githubcopilot.com/models

Choose an ID with ``/chat/completions`` in ``supported_endpoints`` and support
for images and tools. The native Pydantic AI Copilot adapter currently uses
Chat Completions, so models served only through Responses are not supported.
Use the exact catalog ID, including dots.

The default endpoint is ``https://api.githubcopilot.com`` (no ``/v1``).
For an enterprise endpoint or your own gateway, use ``--model-base-url`` or
``agent.model_base_url``. Without an explicit endpoint, the native provider
reads ``GITHUB_COPILOT_BASE_URL``, ``COPILOT_API_URL`` or
``GITHUB_COPILOT_API_BASE`` before using its default. Authentication or
entitlement failures are surfaced; there is no fallback to a paid OpenAI API.

See `Pydantic AI's Copilot documentation
<https://ai.pydantic.dev/models/github-copilot/>`_ for token support and
application-owned device authorization.

OpenWebUI
---------

Use the project-specific ``openwebui:`` prefix and an OpenWebUI API key
from **Settings > Account**. API key access must be enabled by the instance
administrator and permitted for your account.

.. code-block:: sh

   export OPENWEBUI_API_KEY=...
   export OPENWEBUI_BASE_URL=http://localhost:3000/api
   uv run qibocal-agent --model openwebui:YOUR_MODEL_ID \
     --platform YOUR_PLATFORM "YOUR_BOUNDED_OBJECTIVE"

The API base URL must include ``/api`` (and any deployment path prefix);
the client appends ``/chat/completions``. Do not supply the complete
``/api/chat/completions`` URL or assume an OpenAI ``/v1`` suffix.
Use HTTPS for remote instances. The model ID is the exact ID returned by
``GET /api/models``, including any slashes or colons.

.. code-block:: sh

   curl -H "Authorization: Bearer $OPENWEBUI_API_KEY" \
     http://localhost:3000/api/models

The adapter uses OpenAI **Chat Completions**, not Responses, and preserves
tool calls and inline report images. The chosen backend model, OpenWebUI
configuration and any filters must support and preserve both capabilities.
OpenWebUI's API key is separate from any upstream provider key managed by
the instance. ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL`` are not used for
``openwebui:`` models, preventing accidental requests to a different service.
Missing OpenWebUI credentials or endpoint settings are errors.

You can persist the nonsecret endpoint and model:

.. code-block:: sh

   uv run qibocal-mcp-config set agent.model openwebui:YOUR_MODEL_ID
   uv run qibocal-mcp-config set agent.model_base_url http://localhost:3000/api
   uv run qibocal-agent --platform YOUR_PLATFORM "YOUR_BOUNDED_OBJECTIVE"

Endpoint precedence is ``--model-base-url`` > ``agent.model_base_url`` >
``OPENWEBUI_BASE_URL``. There is no implicit localhost endpoint.
Do not embed credentials in endpoint URLs.

See `OpenWebUI's API documentation
<https://docs.openwebui.com/reference/api-endpoints/>`_ for API key access,
model discovery and deployment details.

Other Pydantic AI providers
---------------------------

Existing native identifiers such as ``openai:gpt-4.1`` remain supported
unchanged, with their provider's usual environment settings. For example,
``OPENAI_API_KEY`` configures OpenAI, and ``OPENAI_BASE_URL`` overrides its
endpoint. Use ``openai-chat:MODEL_ID`` for an OpenAI-compatible endpoint
that only exposes Chat Completions.

``--model-base-url`` is reserved for ``openwebui:`` and ``github-copilot:``.
Unset ``agent.model_base_url`` when switching to another provider; an
inapplicable explicit endpoint is an error, not an ignored setting.
Other provider extras must be installed separately as needed.
