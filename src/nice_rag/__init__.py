"""rag-server 서비스 — PostgreSQL 기반 HSCode/문서 RAG REST API.

LLM / 임베딩 백엔드는 ``clients.llm`` / ``clients.embed`` 가 OpenAI-호환
base_url 만 바라보므로, 자체 ollama/vLLM/TEI → 외부 OpenAI/Claude proxy 로의
전환이 환경변수 1줄 변경으로 끝난다.
"""

__version__ = "0.1.0"
