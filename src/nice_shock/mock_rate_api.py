"""backend rate API 목업 — 실스펙 확정 전 개발/시연용 스탠드인 (DB 의존 없음).

nice_shock.rate_client 가 가정하는 계약을 그대로 구현한다:
  GET /rate?upche_cd=<업체코드>&hscode=<품목코드>
    200 → {"rate": 0.xxxx}   # (upche_cd, hscode) 조합에 결정적 — 같은 입력이면 항상 같은 값
    404 → 거래 없음          # hscode 가 '0000' 으로 시작하면 시뮬레이션 (테스트/시연용 규약)
  GET /health

rate 값은 md5(upche_cd|hscode) 기반 0.05~0.95 균등 매핑 — 실데이터가 아니라
**형상 검증용 가짜 값**이다. 실 backend API 가 준비되면 이 서비스를 내리고
RATE_API_URL 만 그쪽으로 바꾸면 된다 (shock-server 코드 수정 불요).

실행:
  uvicorn nice_shock.mock_rate_api:app --host 0.0.0.0 --port 8010
  (compose: rate-mock 서비스 — shock-server 이미지 재사용, command 만 교체)
"""
from __future__ import annotations

import hashlib

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="NICE rate-mock",
    version="0.1.0",
    description="backend rate API 목업 — (upche_cd, hscode) → 수출입 비중 rate(0~1) 결정적 가짜 값.",
)


class RateOut(BaseModel):
    rate: float = Field(..., description="해당 HS 품목의 수출입 비중 (0~1, 결정적 가짜 값)")


@app.get("/rate", response_model=RateOut, summary="(upche_cd, hscode) → rate 조회 (목업)")
def rate(upche_cd: str, hscode: str) -> RateOut:
    if hscode.startswith("0000"):
        raise HTTPException(
            status_code=404,
            detail=f"(upche_cd={upche_cd}, hscode={hscode}) 거래 없음 (목업 규약: '0000' 접두)",
        )
    digest = hashlib.md5(f"{upche_cd}|{hscode}".encode()).hexdigest()
    value = 0.05 + (int(digest[:8], 16) / 0xFFFFFFFF) * 0.90
    return RateOut(rate=round(value, 4))


@app.get("/health", summary="헬스체크")
def health() -> dict:
    return {"status": "ok", "service": "rate-mock"}
