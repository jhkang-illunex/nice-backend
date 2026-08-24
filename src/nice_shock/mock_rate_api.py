"""backend /trade/weight 목업 — 실스펙(2026-08-20 postman) 형상의 개발/시연용 스탠드인.

nice_shock.rate_client 가 사용하는 실계약을 그대로 구현한다:
  POST /trade/weight
    body → {"bseYr", "upchecdList", "hskcodeList"}
    200  → {"status": 0, "data": [{"bseYr", "tscdcg": "H10", "upchecd",
             "weightList": [{"tseximdivcd": "0"|"3", "tscdvl", "tstrdwgt": "0.xxxxxx"}]}],
            "message": null}
  GET /health

목업 규약 (테스트/시연용):
  - hskcode 가 '0000' 으로 시작하면 그 품목은 전 업체 실적 없음 → 행 부재.
  - upchecd 가 '0000' 으로 시작하면 그 업체는 전 품목 실적 없음 → weightList 빈 배열.
  - 그 외 셀은 수출("0")·수입("3") 두 방향 모두 행 생성.

tstrdwgt 는 md5(upchecd|hskcode|exim) 기반 0.05~0.95 균등 매핑 — 실데이터가 아니라
**형상 검증용 가짜 값**이다 (같은 입력이면 항상 같은 값, 방향별로 다른 값). 실 API 가
준비되면 이 서비스를 내리고 RATE_API_URL 만 그쪽 /trade/weight 로 바꾸면 된다
(shock-server 코드 수정 불요).

실행:
  uvicorn nice_shock.mock_rate_api:app --host 0.0.0.0 --port 8010
  (compose: rate-mock 서비스 — shock-server 이미지 재사용, command 만 교체)
"""
from __future__ import annotations

import hashlib

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(
    title="NICE rate-mock",
    version="0.2.0",
    description="backend /trade/weight 목업 — (업체×품목×방향) 거래비중 결정적 가짜 값.",
)

_EXIMS = ("0", "3")  # 0=전체수출 / 3=전체수입


class TradeWeightRequest(BaseModel):
    bseYr: str = Field(..., description="기준년도 (예: '2025')")
    upchecdList: list[str] = Field(..., description="업체코드 목록")
    hskcodeList: list[str] = Field(..., description="HS 10자리 코드 목록")


def _mock_rate(upchecd: str, hskcode: str, exim: str) -> float:
    digest = hashlib.md5(f"{upchecd}|{hskcode}|{exim}".encode()).hexdigest()
    return 0.05 + (int(digest[:8], 16) / 0xFFFFFFFF) * 0.90


@app.post("/trade/weight", summary="기업별 년도별 hskcode별 수출/수입 비중 조회 (목업)")
def trade_weight(req: TradeWeightRequest) -> dict:
    data = []
    for upchecd in req.upchecdList:
        weight_list = []
        if not upchecd.startswith("0000"):
            for hskcode in req.hskcodeList:
                if hskcode.startswith("0000"):
                    continue  # 목업 규약: 실적 없음 → 행 부재
                for exim in _EXIMS:
                    weight_list.append({
                        "tseximdivcd": exim,
                        "tscdvl": hskcode,
                        "tstrdwgt": f"{_mock_rate(upchecd, hskcode, exim):.6f}",
                    })
        data.append({
            "bseYr": req.bseYr,
            "tscdcg": "H10",
            "upchecd": upchecd,
            "weightList": weight_list,
        })
    return {"status": 0, "data": data, "message": None}


@app.get("/health", summary="헬스체크")
def health() -> dict:
    return {"status": "ok", "service": "rate-mock"}
