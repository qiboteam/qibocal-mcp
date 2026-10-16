"""Model selection for subscription and OpenAI-compatible providers."""

import os

from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.github_copilot import GitHubCopilotModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.github_copilot import GitHubCopilotProvider
from pydantic_ai.providers.openai import OpenAIProvider


def resolve_model(identifier: str, *, base_url: str | None = None) -> Model:
    """Keep native model identifiers, with explicit OpenWebUI chat routing."""
    provider, _, name = identifier.partition(":")
    if provider in ("openwebui", "github-copilot"):
        if not name.strip():
            raise UserError(f"{provider}: requires a model ID")
        if provider == "github-copilot":
            return GitHubCopilotModel(
                name, provider=GitHubCopilotProvider(base_url=base_url)
            )
        url = base_url or os.environ.get("OPENWEBUI_BASE_URL")
        key = os.environ.get("OPENWEBUI_API_KEY")
        if not url or not url.strip():
            raise UserError(
                "Set --model-base-url (including /api) or OPENWEBUI_BASE_URL "
                "to use OpenWebUI"
            )
        if not key or not key.strip():
            raise UserError("Set OPENWEBUI_API_KEY to use OpenWebUI")
        return OpenAIChatModel(name, provider=OpenAIProvider(base_url=url, api_key=key))
    if base_url is not None:
        raise UserError(
            "--model-base-url is only supported for openwebui: and github-copilot:; "
            "use the native provider's environment settings for other models"
        )
    return infer_model(identifier)
