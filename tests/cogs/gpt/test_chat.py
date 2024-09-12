from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from openai.types import CompletionUsage, Image
from openai.types.chat.chat_completion import ChatCompletion, Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.images_response import ImagesResponse

from cogs.gpt.utils import ChatGPT

# ------------------------------- fixture -------------------------------


@pytest_asyncio.fixture(scope="session")
async def chatbot():
    return ChatGPT(api_key="fake_key")


@pytest_asyncio.fixture(scope="function")
async def fake_chat_response() -> ChatCompletion:
    return ChatCompletion(
        id="fake_id",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content="I'm good, how are you?",
                    refusal=None,
                    role="assistant",
                    tool_calls=None,
                    function_call=None,
                ),
            )
        ],
        created=15,
        model="gpt-4o",
        object="chat.completion",
        usage=CompletionUsage(
            total_tokens=15,
            completion_tokens=8,
            prompt_tokens=7,
        ),
    )


@pytest_asyncio.fixture(scope="function")
async def fake_image_response() -> ImagesResponse:
    return ImagesResponse(
        data=[
            Image(
                url="https://example.com/cat.jpg",
                b64_json="",
                revised_prompt="Image of a cat",
            )
        ],
        created=15,
    )


@pytest_asyncio.fixture(scope="function")
async def gpt_patcher(
    monkeypatch: pytest.MonkeyPatch,
    fake_chat_response: ChatCompletion,
    fake_image_response: ImagesResponse,
    chatbot: ChatGPT,
):
    monkeypatch.setattr(chatbot, "_send_message", AsyncMock(return_value=fake_chat_response))
    monkeypatch.setattr(chatbot, "detect_malicious_content", AsyncMock(return_value=False))
    monkeypatch.setattr(
        chatbot.client.images, "generate", AsyncMock(return_value=fake_image_response)
    )
    yield monkeypatch


# ------------------------------- test -------------------------------


@pytest.mark.asyncio
async def test_ask(
    chatbot: ChatGPT,
    gpt_patcher: pytest.MonkeyPatch,
):
    EXPECT_TOKENS = 15
    EXPECT_ANSWER = "I'm good, how are you?"

    answer, usage = await chatbot.ask("How are you?")
    assert answer == EXPECT_ANSWER
    assert usage.total_tokens == EXPECT_TOKENS


@pytest.mark.asyncio
async def test_ask_malicious_content(chatbot: ChatGPT, gpt_patcher: pytest.MonkeyPatch):
    gpt_patcher.setattr(chatbot, "detect_malicious_content", AsyncMock(return_value=True))
    with pytest.raises(ValueError):
        await chatbot.ask("I'm going to hack you")


@pytest.mark.asyncio
async def test_create_images(
    chatbot: ChatGPT,
    gpt_patcher: pytest.MonkeyPatch,
):
    results = await chatbot.generate_images("A picture of a cat", "dall-e-2")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_create_images_malicious_content(chatbot: ChatGPT, gpt_patcher: pytest.MonkeyPatch):
    gpt_patcher.setattr(chatbot, "detect_malicious_content", AsyncMock(return_value=True))
    with pytest.raises(ValueError):
        await chatbot.generate_images("I'm going to hack you", "dall-e-2")
