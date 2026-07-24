# think 모델(qwen3 등) 대응 — RAG `<think>` 출력 처리

> **무엇**: qwen3:14b 처럼 `<think>추론</think>` 를 출력하는 reasoning 모델을 LLM 으로 붙였을 때
> RAG 가 오작동하던 것을 `nice_llm.client.chat()` 공통 관문에서 `<think>` 를 제거해 해결.
> **적용 파일**: `src/nice_llm/client.py` (1개). rag-server·ingestion 이미지에 포함.

---

## 1. 증상 (수정 전) — think 모델에서 3중 오작동

| # | 위치 | 증상 |
|---|---|---|
| ① | `chat_json` → `parse_json_lenient` | think 안 중괄호가 JSON 추출을 오염 → `json.loads` 실패 → 빈 dict. **품목추출·CRAG·분류가 조용히 무력화** |
| ② | `/agent` 답변 (`hsk.py`) | `chat()` 원문 반환 → 답변에 `<think>추론</think>` **그대로 노출**, `max_tokens` 소진 시 답변 잘림 |
| ③ | 전반 | thinking 긴 출력 → 생성 느림 → `ReadTimeout` |

비-think 모델(OpenAI/qwen2.5 등)은 태그가 없어 문제 없음 → **어떤 모델이 올지 모르는 배포**에서 안전 확보가 목적.

## 2. 수정 내용 — `chat()` 에서 `<think>` 무조건 제거

`nice_llm.client.chat()` 이 모든 LLM 호출(extract·CRAG·classify·agent)의 공통 관문이므로,
여기서 한 번 청소하면 ①·② 동시 해결. 비-think 모델엔 태그가 없어 **무해한 no-op**(모델 감지 불필요).

```python
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

def strip_reasoning(content: str) -> str:
    if not content:
        return content
    cleaned = _THINK_RE.sub("", content)
    if "</think>" in cleaned:              # 여는 태그 없이 닫힘만 오는 변형 대비
        cleaned = cleaned.rsplit("</think>", 1)[-1]
    return cleaned.strip()
```
`chat()` 의 `return data[...]["content"]` 를 `return strip_reasoning(data[...]["content"])` 로 변경.

---

## 3. 수작업 수정 가이드 (에어갭 — git pull 불가 시)

수정 대상 파일 **딱 1개**: `nice_llm/client.py`. 아래 **3곳**을 고친다.

### 3-A. 정확한 변경 3곳 (before → after)

**(1) import 추가** — 상단 import 블록:
```python
 import json
 import logging
+import re
 from dataclasses import dataclass
```

**(2) 함수 추가** — `log = logging.getLogger(__name__)` 아래에 삽입:
```python
 log = logging.getLogger(__name__)
+
+
+_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
+
+
+def strip_reasoning(content: str) -> str:
+    """<think>...</think> 추론 블록 제거. 비-think 모델은 no-op."""
+    if not content:
+        return content
+    cleaned = _THINK_RE.sub("", content)
+    if "</think>" in cleaned:
+        cleaned = cleaned.rsplit("</think>", 1)[-1]
+    return cleaned.strip()
```

**(3) chat() 반환부 변경** — `chat()` 메서드 마지막 줄:
```python
             data = r.json()
-        return data["choices"][0]["message"]["content"]
+        return strip_reasoning(data["choices"][0]["message"]["content"])
```

### 3-B. 어디를 고치나 — 실행 코드 경로

이미지 안에서 실제 임포트되는 파일은 **pip 설치본**(소스 `/app/src` 아님):
```bash
docker run --rm --entrypoint python nice/rag-server:dev \
  -c "import nice_llm.client, inspect; print(inspect.getfile(nice_llm.client))"
# → /usr/local/lib/python3.11/site-packages/nice_llm/client.py
```
(파이썬 버전이 다르면 위 명령이 알려주는 실제 경로를 쓸 것.)

### 방법 A) 소스 수정 + 이미지 재빌드 (권장 — 빌드 가능 환경)
```bash
# src/nice_llm/client.py 를 위 (1)(2)(3) 대로 수정 후
docker compose build rag-server ingestion
# 재저장(에어갭 반출용)
docker save nice/rag-server:dev nice/shock-server:dev nice/ingestion:dev | gzip > nice_ai_app.tar.gz
```

### 방법 B) 재빌드 불가 — 실행 이미지 직접 패치 + commit
빌드 없이, 로드된 이미지의 파일을 꺼내 고쳐 넣고 `docker commit` 으로 굳힌다.
```bash
IMG=nice/rag-server:dev
F=/usr/local/lib/python3.11/site-packages/nice_llm/client.py   # 3-B 로 확인한 실제 경로

docker create --name _patch $IMG
docker cp _patch:$F ./client.py            # 꺼내기
#  ── ./client.py 를 위 (1)(2)(3) 대로 손으로 수정 ──
docker cp ./client.py _patch:$F            # 넣기 (root 소유 유지됨)
docker commit _patch $IMG                  # 같은 태그로 굳힘
docker rm _patch
# ingestion 이미지도 nice_llm 포함 → LLM 을 쓰면 동일 절차로 패치
```
패치 후 **컨테이너 재생성**해야 반영:
```bash
docker compose -f docker-compose.deploy.yml up -d --force-recreate rag-server
```

---

## 4. 검증

```bash
# (a) 단위 테스트 (빌드/개발 환경)
pytest tests/test_think_model.py -q            # 6 passed

# (b) 실제 응답에 <think> 안 나오는지 (배포 후) — /agent 답변 확인
curl -s "http://localhost:18002/api/hsk/agent?q=밸브" | python3 -c \
  'import sys,json; a=json.load(sys.stdin)["answer"]; print("has <think>:", "<think>" in a); print(a[:120])'
#   → has <think>: False 면 정상
```

## 5. (선택) 모델측 no-think 병행 — 속도/비용

코드 수정은 ①②(파싱·누출)를 막지만, ③(느림)은 thinking 자체가 원인이라 남는다.
생성을 빠르게 하려면 ollama 에서 thinking 을 끈 파생 모델을 쓴다(코드 수정과 **병행 권장**):
```bash
docker exec -i nice-llm sh -c 'printf "FROM qwen3:14b\nSYSTEM /no_think\n" | ollama create qwen3-nothink -f -'
# rag .env:  LLM_MODEL=qwen3-nothink
```
> 단, no-think 로도 빈 `<think></think>` 를 남기는 버전이 있어 **코드 strip 은 그대로 두는 게 안전**하다.
> (모델 설정=이 배포 한정·가변 / 코드 strip=전 모델·영구.)

---

관련: `nice_llm/client.py`, `tests/test_think_model.py`, [`RUNBOOK_설치.md`](RUNBOOK_설치.md).
