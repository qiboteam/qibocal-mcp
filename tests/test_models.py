"""Provider selection and tool/image round trips without live model credentials."""

import json
import os
import unittest
from functools import partial
from unittest.mock import patch

import httpx2
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelHTTPError, UserError
from pydantic_ai.models.github_copilot import GitHubCopilotModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.github_copilot import GitHubCopilotProvider
from pydantic_ai.providers.openai import OpenAIProvider

from qibocal_mcp.models import resolve_model


class ModelTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_native_models_are_preserved(self):
        self.assertIsInstance(resolve_model("test"), TestModel)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai"}):
            self.assertIsInstance(resolve_model("openai:gpt-4.1"), OpenAIResponsesModel)
            self.assertIsInstance(resolve_model("openai-chat:gpt-4.1"), OpenAIChatModel)

    def test_copilot_uses_subscription_provider_and_ignores_openai_settings(self):
        with patch.dict(
            os.environ,
            {
                "GITHUB_COPILOT_API_KEY": "test-copilot",
                "OPENAI_API_KEY": "unrelated",
                "OPENAI_BASE_URL": "https://unrelated.example/v1",
            },
        ):
            model = resolve_model("github-copilot:claude-haiku-4.5")
        self.assertIsInstance(model, GitHubCopilotModel)
        self.assertEqual(model.system, "github-copilot")
        self.assertEqual(model.base_url.rstrip("/"), "https://api.githubcopilot.com")
        self.assertEqual(model.client.api_key, "test-copilot")
        self.assertEqual(model.model_name, "claude-haiku-4.5")

    def test_copilot_token_aliases_and_endpoint_override(self):
        for variable in ("GITHUB_COPILOT_API_TOKEN", "COPILOT_GITHUB_TOKEN"):
            with self.subTest(variable=variable), patch.dict(
                os.environ,
                {variable: "test-copilot", "GITHUB_COPILOT_BASE_URL": "https://env.example"},
            ):
                model = resolve_model(
                    "github-copilot:gpt-5.4", base_url="https://explicit.example"
                )
                self.assertEqual(model.client.api_key, "test-copilot")
                self.assertEqual(model.base_url.rstrip("/"), "https://explicit.example")

    def test_general_github_tokens_are_not_used_for_copilot(self):
        with patch.dict(os.environ, {"GH_TOKEN": "unrelated", "GITHUB_TOKEN": "unrelated"}):
            with self.assertRaisesRegex(UserError, "GITHUB_COPILOT_API_KEY"):
                resolve_model("github-copilot:claude-haiku-4.5")

    def test_openwebui_uses_chat_and_dedicated_settings(self):
        with patch.dict(
            os.environ,
            {
                "OPENWEBUI_BASE_URL": "http://localhost:3000/api",
                "OPENWEBUI_API_KEY": "test-webui",
                "OPENAI_API_KEY": "unrelated",
                "OPENAI_BASE_URL": "https://unrelated.example/v1",
            },
        ):
            model = resolve_model("openwebui:custom/model:latest")
            explicit = resolve_model(
                "openwebui:custom/model:latest", base_url="https://explicit.example/api"
            )
        self.assertIsInstance(model, OpenAIChatModel)
        self.assertNotIsInstance(model, OpenAIResponsesModel)
        self.assertEqual(model.model_name, "custom/model:latest")
        self.assertEqual(model.base_url.rstrip("/"), "http://localhost:3000/api")
        self.assertEqual(model.client.api_key, "test-webui")
        self.assertEqual(explicit.base_url.rstrip("/"), "https://explicit.example/api")

    def test_missing_openwebui_settings_are_errors(self):
        with self.assertRaisesRegex(UserError, "OPENWEBUI_BASE_URL"):
            resolve_model("openwebui:model")
        with self.assertRaisesRegex(UserError, "OPENWEBUI_API_KEY"):
            resolve_model("openwebui:model", base_url="http://localhost:3000/api")
        with patch.dict(
            os.environ,
            {"OPENWEBUI_API_KEY": " ", "OPENAI_API_KEY": "unrelated"},
        ):
            with self.assertRaisesRegex(UserError, "OPENWEBUI_API_KEY"):
                resolve_model("openwebui:model", base_url="http://localhost:3000/api")

    def test_empty_model_ids_and_unrelated_endpoint_overrides_are_errors(self):
        for identifier in ("openwebui:", "github-copilot:", "openwebui: "):
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(UserError, "requires a model ID"):
                    resolve_model(identifier)
        with self.assertRaisesRegex(UserError, "only supported"):
            resolve_model("test", base_url="http://localhost:3000/api")


class ProviderRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_providers_send_images_and_complete_tool_round_trips(self):
        for identifier, constructor, environment, path, token in (
            (
                "openwebui:custom-model", OpenAIProvider,
                {"OPENWEBUI_API_KEY": "test-webui"},
                "/api/chat/completions", "test-webui",
            ),
            (
                "github-copilot:claude-haiku-4.5", GitHubCopilotProvider,
                {"GITHUB_COPILOT_API_KEY": "test-copilot"},
                "/chat/completions", "test-copilot",
            ),
        ):
            with self.subTest(identifier=identifier):
                requests = []

                def respond(request):
                    requests.append(request)
                    message = (
                        {
                            "role": "assistant", "content": None,
                            "tool_calls": [{
                                "id": "call-1", "type": "function",
                                "function": {"name": "inspect_fit", "arguments": "{}"},
                            }],
                        }
                        if len(requests) == 1
                        else {"role": "assistant", "content": "Leave the platform unchanged."}
                    )
                    return httpx2.Response(
                        200,
                        json={
                            "id": "response-1", "object": "chat.completion",
                            "created": 1, "model": identifier.partition(":")[2],
                            "choices": [{
                                "index": 0, "message": message,
                                "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                            }],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                        },
                    )

                async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch(
                            f"qibocal_mcp.models.{constructor.__name__}",
                            side_effect=partial(constructor, http_client=client),
                        ),
                    ):
                        base_url = "https://model.example/api" if identifier.startswith("openwebui:") else "https://model.example"
                        model = resolve_model(identifier, base_url=base_url)
                        calibration = Agent(model)

                        @calibration.tool_plain
                        def inspect_fit() -> str:
                            return "Fit uncertainty is too large."

                        result = await calibration.run([
                            "Inspect this report.",
                            BinaryContent(data=b"synthetic-image", media_type="image/png"),
                        ])
                self.assertEqual(result.output, "Leave the platform unchanged.")
                self.assertEqual(len(requests), 2)
                self.assertTrue(all(request.url.path == path for request in requests))
                self.assertTrue(all(
                    request.headers["authorization"] == f"Bearer {token}"
                    for request in requests
                ))
                first = json.loads(requests[0].content)
                self.assertEqual(first["model"], identifier.partition(":")[2])
                self.assertEqual(first["tools"][0]["function"]["name"], "inspect_fit")
                image = first["messages"][0]["content"][1]
                self.assertEqual(image["type"], "image_url")
                self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))
                second = json.loads(requests[1].content)
                tool = next(message for message in second["messages"] if message["role"] == "tool")
                self.assertEqual(tool["tool_call_id"], "call-1")
                self.assertIn("uncertainty", tool["content"])
                if identifier.startswith("github-copilot:"):
                    self.assertEqual(requests[0].headers["copilot-integration-id"], "vscode-chat")

    async def test_authentication_errors_are_not_hidden(self):
        def reject(request):
            return httpx2.Response(401, json={"error": {"message": "Invalid credential"}})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(reject)) as client:
            with (
                patch.dict(os.environ, {"OPENWEBUI_API_KEY": "invalid"}, clear=True),
                patch(
                    "qibocal_mcp.models.OpenAIProvider",
                    side_effect=partial(OpenAIProvider, http_client=client),
                ),
            ):
                model = resolve_model("openwebui:model", base_url="https://model.example/api")
                with self.assertRaises(ModelHTTPError) as error:
                    await Agent(model).run("Inspect")
        self.assertEqual(error.exception.status_code, 401)
