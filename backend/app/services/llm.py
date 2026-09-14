from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam

from app.core.config import Settings


@dataclass
class LLMChatMessage:
    role: str
    content: str


LLMCompletionStatus = Literal["completed", "output_truncated"]


@dataclass
class LLMStreamEvent:
    type: Literal["chunk", "complete"]
    content: str = ""
    completion_status: LLMCompletionStatus = "completed"


class LLMProvider(Protocol):
    def stream_chat(
        self,
        messages: list[LLMChatMessage],
        max_output_tokens: int,
    ) -> AsyncIterator[str]:
        ...

    def stream_chat_events(
        self,
        messages: list[LLMChatMessage],
        max_output_tokens: int,
    ) -> AsyncIterator[LLMStreamEvent]:
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

    def stream_chat_events(
        self,
        messages: list[LLMChatMessage],
        max_output_tokens: int,
    ) -> AsyncIterator[LLMStreamEvent]:
        async def _generator() -> AsyncIterator[LLMStreamEvent]:
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

            finish_reason: str | None = None
            async for chunk in response_stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta.content
                if delta:
                    yield LLMStreamEvent(type="chunk", content=delta)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

            completion_status: LLMCompletionStatus = "output_truncated" if finish_reason == "length" else "completed"
            yield LLMStreamEvent(type="complete", completion_status=completion_status)

        return _generator()


async def stream_chat_events(
    provider: LLMProvider,
    messages: list[LLMChatMessage],
    max_output_tokens: int,
) -> AsyncIterator[LLMStreamEvent]:
    stream_events_fn = getattr(provider, "stream_chat_events", None)
    if callable(stream_events_fn):
        async for event in stream_events_fn(messages=messages, max_output_tokens=max_output_tokens):
            yield event
        return

    async for chunk in provider.stream_chat(messages=messages, max_output_tokens=max_output_tokens):
        yield LLMStreamEvent(type="chunk", content=chunk)
    yield LLMStreamEvent(type="complete", completion_status="completed")


def get_llm_provider(settings: Settings) -> LLMProvider:
    return OpenAILLMProvider(settings)
