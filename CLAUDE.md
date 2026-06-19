# nice-backend — 프로젝트 가이드

## 쇼크 전파(propagation) 로직 — 어느 구현이 "실제" 계산인가

전파 계산 코드가 두 군데에 존재하므로 혼동 주의.

### 실제 계산 엔진: `src/nice_graph/shock/propagate.py`

- graph-analysis 서버(`nice_graph`)가 쓰는 **현역** 전파 엔진.
- 알고리즘: round-by-round 거듭제곱급수 합 `Σ_k R^k @ init` (active-set 반복법).
- 데이터: 운영 PG `public.node` / `public.edge` (read-only SELECT).
- 수렴: edge `rate` 가 source 별 outgoing 정규화(Σ_out ≤ 1)라 ρ(R) ≤ 1 로 절대 수렴.

### 불용(참고 자료 한정): `src/nice_poc/propagation/` (leontief, bicgstab)

- **NICE 가 제공한 자료에 포함돼 있어 코드로 들어와 있을 뿐, 실제 계산 용도로는 사용되지 않음.**
- Leontief 역행렬 직접해법. 실제 운영/서버 전파 계산 경로가 아니다.
- 신규 작업 시 이 루틴을 계산 기준으로 삼지 말 것. 전파 계산은 위 `nice_graph/shock/propagate.py` 가 단일 기준.
- 참고: `src/nice_demo/pipeline/shock_runner.py` 가 `nice_poc.propagation.leontief` 를 import 하지만,
  이는 Streamlit **데모 시연용**일 뿐 실제 계산 엔진이 아니다. (삭제 시 데모가 깨지므로 정리 전 데모 의존부터 확인.)

### 문서-코드 드리프트 주의

`src/nice_graph/__init__.py` docstring 의 "Neo4j 기반 … `nice_poc.propagation` import" 설명은
과거 설계 잔재로, 현재 구현(PG 기반 자체 `propagate.py`)과 불일치한다.
