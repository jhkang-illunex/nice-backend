"""backend 거래비중 일괄 조회 클라이언트 — POST /trade/weight (실스펙, 2026-08-20 수신).

tariff 시나리오가 시드별 주입액 = total_amount(손익계산서 매출액) × Σrate(HS10 품목별
수출입 비중) × shock_rate(충격 비율) 를 만들 때 쓰는 rate 를 backend API 에서 조회한다.
조회 키는 seed_id(사업자번호)가 아니라 **upchecd(업체코드)** + hskcode(HS 10자리).
DB 의존 없음(httpx only) — nice_shock 의 stateless 원칙 유지.

실계약 (docs/20260820_[NICE] 관계망.postman_collection.json — "거래비중"):
  POST {RATE_API_URL}
  body → {"bseYr": "2025", "upchecdList": [...], "hskcodeList": [...]}
  200  → {"status": 0,
          "data": [{"bseYr", "tscdcg": "H10", "upchecd",
                    "weightList": [{"tseximdivcd": "0"|"3",   # 0=전체수출 / 3=전체수입
                                    "tscdvl": "<HS10>",       # 무역통계코드(HS 10자리)
                                    "tstrdwgt": "0.272135"}]  # 거래비중(문자열, 0~1)
                   }],
          "message": null}
  실적 없는 (업체, 품목, 방향) 셀은 응답에 행이 없다 — 404 가 아니라 **행 부재**.

환경변수
  RATE_API_URL     : 필수. /trade/weight 전체 URL (예: http://backend:8080/trade/weight).
                     미설정이면 tariff 호출 전체가 503.
  RATE_API_TIMEOUT : 초 (기본 5.0).

개발/시연용 목업: ``nice_shock.mock_rate_api`` (compose 의 rate-mock 서비스) — 위 계약을
그대로 구현한 결정적 가짜 서버. RATE_API_URL 을 목업으로 지정해 사용.

오류 구분
  RateApiUnavailable : 설정 누락·연결 실패·타임아웃·비200·status≠0·응답 형식 오류 —
                       서비스 수준 문제 → 503.
  셀 수준 문제(값 파싱 불가·0~1 범위 위반)는 그 셀만 버리고(warning 로그) 결과에서
  제외한다 — 행 부재(실적 없음)와 동일하게 취급.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

RATE_API_URL_ENV = "RATE_API_URL"
RATE_API_TIMEOUT_ENV = "RATE_API_TIMEOUT"
_DEFAULT_TIMEOUT = 5.0

# 한국무역통계진흥원 수출입유형코드 — 0:전체수출 / 3:전체수입 (1/2 는 미사용).
EXIM_EXPORT = "0"
EXIM_IMPORT = "3"


class RateApiUnavailable(Exception):
    """backend API 자체에 도달 불가/계약 위반 (미설정·연결·타임아웃·비200·status≠0) — 503 대상."""


def fetch_weights(
    bse_yr: str,
    upchecd_list: list[str],
    hskcode_list: list[str],
    exim: str,
) -> dict[tuple[str, str], float]:
    """기준연도의 (업체×품목) 거래비중을 일괄 조회해 {(upchecd, hskcode): rate} 로 반환.

    exim(EXIM_EXPORT|EXIM_IMPORT) 방향의 행만 취한다. 응답에 행이 없는 셀은 실적 없음 —
    반환 dict 에 키가 없다. 값 오류(비수치·0~1 밖) 셀도 버리고 키를 만들지 않는다.
    """
    url = os.environ.get(RATE_API_URL_ENV)
    if not url:
        raise RateApiUnavailable(f"{RATE_API_URL_ENV} 미설정 — backend /trade/weight 주소가 필요합니다")
    timeout = float(os.environ.get(RATE_API_TIMEOUT_ENV, _DEFAULT_TIMEOUT))
    body = {"bseYr": bse_yr, "upchecdList": upchecd_list, "hskcodeList": hskcode_list}
    try:
        resp = httpx.post(url, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        raise RateApiUnavailable(f"backend /trade/weight 연결 실패: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise RateApiUnavailable(f"backend /trade/weight HTTP {resp.status_code}")
    try:
        payload = resp.json()
        status = payload["status"]
        data = payload["data"] or []
    except (ValueError, KeyError, TypeError) as exc:
        raise RateApiUnavailable(f"/trade/weight 응답 형식 오류: {exc.__class__.__name__}") from exc
    if status != 0:
        raise RateApiUnavailable(
            f"/trade/weight status={status}: {payload.get('message')}"
        )
    weights: dict[tuple[str, str], float] = {}
    for row in data:
        upchecd = row.get("upchecd")
        for w in row.get("weightList") or []:
            if w.get("tseximdivcd") != exim:
                continue
            hskcode = w.get("tscdvl")
            try:
                rate = float(w.get("tstrdwgt"))
            except (ValueError, TypeError):
                log.warning("trade/weight 셀 값 파싱 불가 — 버림: upchecd=%s tscdvl=%s tstrdwgt=%r",
                            upchecd, hskcode, w.get("tstrdwgt"))
                continue
            if not 0.0 <= rate <= 1.0:
                log.warning("trade/weight 셀 rate 범위(0~1) 위반 — 버림: upchecd=%s tscdvl=%s rate=%s",
                            upchecd, hskcode, rate)
                continue
            weights[(upchecd, hskcode)] = rate
    return weights
