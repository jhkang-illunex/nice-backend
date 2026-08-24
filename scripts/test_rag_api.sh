#!/usr/bin/env bash
# rag-server API 스모크 테스트 — 로컬/사내망 전용, 외부 인터넷 불요(curl + python3 표준 라이브러리만 사용).
# 사용: ./scripts/test_rag_api.sh [질의어]
# 환경변수로 대상 조정: RAG_HOST(기본 localhost), RAG_API_PORT(기본 18002, .env 의 값과 맞출 것)
set -uo pipefail

HOST="${RAG_HOST:-localhost}"
PORT="${RAG_API_PORT:-18002}"
BASE="http://${HOST}:${PORT}"
QUERY="${1:-밸브}"

hr() { printf '\n%s\n' "──────────────────────────────────────────────────"; }
# 정렬 출력 + 한글 유지(--no-ensure-ascii, python3.9+). 실패 시 원문 그대로.
pp() { python3 -m json.tool --no-ensure-ascii 2>/dev/null || python3 -m json.tool 2>/dev/null || cat; }

# $1=설명 $2=타임아웃(초) $3=경로 $4..=curl --data-urlencode 인자("key=value")
# -G --data-urlencode 로 한글 등 non-ASCII 쿼리를 자동 URL 인코딩(안 하면 서버가 400 리턴).
call() {
    local desc="$1" timeout="$2" path="$3"; shift 3
    local -a qs=(); for kv in "$@"; do qs+=(--data-urlencode "${kv}"); done
    hr; echo "[${desc}] GET ${BASE}${path}  (${*})"
    local tmp; tmp="$(mktemp)"
    local code
    code=$(curl -s -G "${qs[@]}" -o "${tmp}" -w '%{http_code}' --max-time "${timeout}" "${BASE}${path}")
    [ "${code}" = "000" ] && echo "요청 실패(연결 불가 또는 ${timeout}s 타임아웃)" || { echo "HTTP ${code}"; pp < "${tmp}"; }
    rm -f "${tmp}"
}

echo "대상: ${BASE}   질의어: \"${QUERY}\""

# 1) 의존성 3종(postgres/embed/llm) 연결 확인 — 여기서 fail 이면 아래도 다 실패함
call "1/3 헬스체크"   30  "/health/deep"

# 2) 벡터 검색 — 임베딩만 필요, LLM 불통이어도 degrade 로 결과는 나옴
call "2/3 검색"       30  "/api/hsk/search" "q=${QUERY}" "limit=5"

# 3) LLM 답변 — CRAG 보정 포함. thinking 모델은 LLM_REASONING_EFFORT 미설정 시 매우 느려질 수
#    있어 타임아웃을 넉넉히 잡음(.env 의 LLM_TIMEOUT_S 와 맞출 것). 상세: THINK_모델_대응.md
call "3/3 에이전트"   120 "/api/hsk/agent" "q=${QUERY}"

hr; echo "완료."
