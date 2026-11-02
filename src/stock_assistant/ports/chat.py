"""Ports composing evidence-grounded assistant chat without model coupling."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.chat import (
    ChatGroundingRequest,
    ChatGroundingResult,
    ChatHistoryPage,
    ChatHistoryQuery,
    ChatIntentRequest,
    ChatIntentResult,
    ChatRequest,
    ChatResponse,
    ChatRetrievalRequest,
    ChatRetrievalResult,
    ChatTurn,
    ChatTurnWriteRequest,
)


@runtime_checkable
class ChatIntentClassifierPort(Protocol):
    async def classify(self, request: ChatIntentRequest) -> ChatIntentResult: ...


@runtime_checkable
class ChatEvidenceRetrievalPort(Protocol):
    async def retrieve(self, request: ChatRetrievalRequest) -> ChatRetrievalResult: ...


@runtime_checkable
class ChatGroundingPort(Protocol):
    async def validate(self, request: ChatGroundingRequest) -> ChatGroundingResult: ...


@runtime_checkable
class ChatAgentPort(Protocol):
    async def respond(self, request: ChatRequest) -> ChatResponse: ...


@runtime_checkable
class ChatHistoryPort(Protocol):
    async def append(self, request: ChatTurnWriteRequest) -> ChatTurn: ...

    async def query(self, request: ChatHistoryQuery) -> ChatHistoryPage: ...
