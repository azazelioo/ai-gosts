from typing import Optional

from app.llm.base import EmbeddingProvider, LLMProvider
from app.llm.local_chat_provider import LocalChatProvider
from app.llm.local_provider import LocalProvider
from app.orchestration.pipeline import DocumentPipeline
from app.standards.registry import StandardRegistry


_chat_provider: Optional[LLMProvider] = None
_embedding_provider: Optional[EmbeddingProvider] = None
_pipeline: Optional[DocumentPipeline] = None
_registry: Optional[StandardRegistry] = None


def resolve_runtime_modes(chat_mode: Optional[str] = None, embed_mode: Optional[str] = None) -> tuple[str, str]:
    for field, value in (("chat_mode", chat_mode), ("embed_mode", embed_mode)):
        if value and value.strip().lower() != "local":
            raise ValueError(f"{field}: доступен только локальный режим.")
    return "local", "local"


def get_chat_provider() -> LLMProvider:
    global _chat_provider
    if _chat_provider is None:
        _chat_provider = LocalChatProvider()
    return _chat_provider


def get_embedding_provider() -> EmbeddingProvider:
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = LocalProvider()
    return _embedding_provider


def get_llm_provider() -> LLMProvider:
    return get_chat_provider()


def get_standard_registry() -> StandardRegistry:
    global _registry
    if _registry is None:
        _registry = StandardRegistry()
    return _registry


def get_pipeline() -> DocumentPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DocumentPipeline(
            llm_provider=get_chat_provider(),
            embedding_provider=get_embedding_provider(),
            registry=get_standard_registry(),
        )
    return _pipeline
