"""OpenAI-호환 백엔드(LLM/Embed) 클라이언트 — base_url 만 바라봄."""

from nice_rag.clients.embed import EmbedClient, get_embed_client
from nice_rag.clients.llm import LlmClient, get_llm_client

__all__ = ["EmbedClient", "LlmClient", "get_embed_client", "get_llm_client"]
