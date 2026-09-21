"""Microsoft Agent Framework runtime helpers shared by application agents."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from agent_framework import (
    Agent,
    AgentResponse,
    AgentResponseUpdate,
    AgentSession,
    Content,
    ContextProvider,
    Message,
    ResponseStream,
)
from agent_framework.openai import OpenAIChatCompletionClient

from ..config import AppConfig, OpenAIConfig


class CompatibleOpenAIChatCompletionClient(OpenAIChatCompletionClient):
    """Preserve reasoning fields used by OpenAI-compatible thinking models.

    MAF 1.11 understands OpenRouter's ``reasoning_details`` extension, while
    DeepSeek-style endpoints return ``reasoning_content`` and require that exact
    value on the assistant tool-call message in the next model request.  Without
    this adapter, the first tool call succeeds and the following model roundtrip
    fails with HTTP 400 because the reasoning prefix was discarded.
    """

    @staticmethod
    def _reasoning_content(value: Any) -> str | None:
        reasoning = getattr(value, "reasoning_content", None)
        return reasoning if isinstance(reasoning, str) and reasoning else None

    def _parse_response_from_openai(self, response: Any, options: Any) -> Any:
        parsed = super()._parse_response_from_openai(response, options)
        for choice, message in zip(response.choices, parsed.messages):
            if reasoning := self._reasoning_content(choice.message):
                message.contents.append(Content.from_text_reasoning(text=reasoning))
        return parsed

    def _parse_response_update_from_openai(self, chunk: Any) -> Any:
        parsed = super()._parse_response_update_from_openai(chunk)
        for choice in chunk.choices:
            if choice.delta is not None and (
                reasoning := self._reasoning_content(choice.delta)
            ):
                parsed.contents.append(Content.from_text_reasoning(text=reasoning))
        return parsed

    def _prepare_message_for_openai(self, message: Message) -> list[dict[str, Any]]:
        reasoning_parts = [
            content.text
            for content in message.contents
            if content.type == "text_reasoning" and content.text
        ]
        if not reasoning_parts or message.role != "assistant":
            return super()._prepare_message_for_openai(message)

        # Text reasoning must not become ordinary assistant content.  Serialize
        # the remaining contents with MAF, then restore the provider extension.
        filtered = Message(
            role=message.role,
            contents=[
                content
                for content in message.contents
                if not (content.type == "text_reasoning" and content.text)
            ],
            author_name=message.author_name,
            message_id=message.message_id,
            additional_properties=message.additional_properties,
            raw_representation=message.raw_representation,
        )
        prepared = super()._prepare_message_for_openai(filtered)
        if not prepared:
            prepared = [{"role": "assistant", "content": ""}]
        prepared[0]["reasoning_content"] = "".join(reasoning_parts)
        return prepared


def create_chat_client(
    *,
    model: Optional[str] = None,
    max_iterations: Optional[int] = None,
    max_function_calls: Optional[int] = None,
) -> OpenAIChatCompletionClient:
    """Create the MAF client for an OpenAI-compatible endpoint."""
    common: dict[str, Any] = {
        "model": model or OpenAIConfig.MODEL,
        "base_url": OpenAIConfig.BASE_URL,
        "api_key": OpenAIConfig.API_KEY,
        "function_invocation_configuration": {
            "enabled": True,
            "max_iterations": (
                max_iterations
                if max_iterations is not None
                else AppConfig.QUERY_ENGINE_MAX_MODEL_ROUNDTRIPS
            ),
            "max_function_calls": (
                max_function_calls
                if max_function_calls is not None
                else AppConfig.QUERY_ENGINE_MAX_FUNCTION_CALLS
            ),
            "max_consecutive_errors_per_request": (
                AppConfig.QUERY_ENGINE_MAX_CONSECUTIVE_ERRORS
            ),
        },
    }
    return CompatibleOpenAIChatCompletionClient(**common)


def create_agent(
    *,
    name: str,
    instructions: str,
    tools: Sequence[Any],
    reasoning_effort: str,
    context_providers: Optional[Sequence[ContextProvider]] = None,
    model: Optional[str] = None,
    max_iterations: Optional[int] = None,
    max_function_calls: Optional[int] = None,
    middleware: Optional[Sequence[Any]] = None,
) -> Agent:
    """Create a MAF Agent while keeping construction consistent across sub-agents."""
    selected_model = model or OpenAIConfig.MODEL
    resolved_tools = list(tools)
    resolved_providers = list(context_providers or [])
    default_options: dict[str, Any] = {}
    # gpt-5 rejects temperature/top_p and exposes reasoning_effort, but Chat Completions
    # refuses reasoning_effort whenever function tools are present; that combination needs
    # the Responses API, which this client does not use. A SkillsProvider also contributes
    # request-level tools, so it disqualifies the option just like an explicit tool.
    if (
        selected_model.strip().lower().startswith("gpt-5")
        and not resolved_tools
        and not resolved_providers
    ):
        default_options["reasoning_effort"] = reasoning_effort
    return create_chat_client(
        model=selected_model,
        max_iterations=max_iterations,
        max_function_calls=max_function_calls,
    ).as_agent(
        name=name,
        instructions=instructions,
        tools=resolved_tools,
        context_providers=resolved_providers,
        default_options=default_options,
        middleware=middleware,
    )


def create_session(agent: Agent) -> AgentSession:
    """Create an in-memory conversation session for an agent."""
    return agent.create_session()


async def run_agent(
    agent: Agent,
    message: str,
    *,
    session: AgentSession | None = None,
) -> AgentResponse:
    """Run an agent without streaming."""
    return await agent.run(message, session=session)


def stream_agent(
    agent: Agent,
    message: str,
    *,
    session: AgentSession | None = None,
) -> ResponseStream[AgentResponseUpdate, AgentResponse[Any]]:
    """Run an agent as an async stream."""
    return agent.run(message, stream=True, session=session)
