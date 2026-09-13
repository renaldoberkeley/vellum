from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam

from app.core.config import Settings


@dataclass
class LLMChatMessage:
    role: str
    content: str


class LLMProvider(Protocol):
    def stream_chat(
        self,
        messages: list[LLMChatMessage],
        max_output_tokens: int,
    ) -> AsyncIterator[str]:
        ...


class OpenAILLMProvider:
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self._model = settings.openai_model
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    def stream_chat(
        self,
        messages: list[LLMChatMessage],
        max_output_tokens: int,
    ) -> AsyncIterator[str]:
        async def _generator() -> AsyncIterator[str]:
            openai_messages: list[ChatCompletionMessageParam] = []
            for message in messages:
                role = message.role if message.role in {"system", "user", "assistant"} else "user"
                openai_messages.append(
                    cast(ChatCompletionMessageParam, {"role": role, "content": message.content})
                )

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=openai_messages,
                stream=True,
                max_tokens=max_output_tokens,
            )
            response_stream = cast(AsyncStream[ChatCompletionChunk], response)

            async for chunk in response_stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

        return _generator()


def get_llm_provider(settings: Settings) -> LLMProvider:
    return OpenAILLMProvider(settings)
