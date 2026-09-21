"""Import and construction smoke tests for the supported MAF runtime."""

from agent_framework import Agent, AgentSession, Content, Message, SkillsProvider
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from agent_framework.openai import OpenAIChatCompletionClient

from src.agents.maf_runtime import create_agent, create_chat_client
from src.config import OpenAIConfig


def test_maf_runtime_imports() -> None:
    client = create_chat_client()

    assert isinstance(client, OpenAIChatCompletionClient)
    assert client.base_url == OpenAIConfig.BASE_URL
    assert client.azure_endpoint is None
    assert client._use_azure_client is False
    assert Agent is not None
    assert AgentSession is not None
    assert SkillsProvider is not None


def test_maf_runtime_can_route_to_small_deployment() -> None:
    client = create_chat_client(model=OpenAIConfig.SMALL_MODEL)

    assert client.model == OpenAIConfig.SMALL_MODEL


def test_thinking_model_reasoning_content_survives_tool_roundtrip() -> None:
    client = create_chat_client()
    chunk = ChatCompletionChunk.model_validate(
        {
            "id": "completion-1",
            "created": 1,
            "model": "deepseek-reasoner",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "reasoning_content": "plan"},
                    "finish_reason": None,
                }
            ],
        }
    )

    update = client._parse_response_update_from_openai(chunk)
    reasoning = next(content for content in update.contents if content.type == "text_reasoning")
    assert reasoning.text == "plan"

    message = Message(
        "assistant",
        [
            Content.from_text_reasoning(text="plan"),
            Content.from_function_call(call_id="call-1", name="lookup", arguments="{}"),
        ],
    )
    prepared = client._prepare_message_for_openai(message)

    assert prepared == [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
            "reasoning_content": "plan",
        }
    ]


def test_gpt5_agents_send_reasoning_effort_not_temperature() -> None:
    toolless = create_agent(
        name="reasoning-effort-test",
        instructions="test",
        tools=[],
        reasoning_effort="low",
        model="gpt-5-test",
    )

    # gpt-5 deployments reject temperature/top_p and expose reasoning_effort instead.
    assert toolless.default_options["reasoning_effort"] == "low"
    assert "temperature" not in toolless.default_options
    assert "top_p" not in toolless.default_options

    def sample_tool() -> str:
        """A tool."""
        return "ok"

    with_tools = create_agent(
        name="reasoning-effort-with-tools",
        instructions="test",
        tools=[sample_tool],
        reasoning_effort="low",
        model="gpt-5-test",
    )

    # Chat Completions rejects reasoning_effort alongside function tools.
    assert "reasoning_effort" not in with_tools.default_options
