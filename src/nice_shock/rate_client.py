"""외부 rate 조회 클라이언트 — (upche_cd, hscode) → 해당 HS 품목의 수출입 비중 rate (0~1).

tariff 시나리오가 시드별 주입액 = total_amount(수출입 금액) × rate(수출입 비중) ×
shock_rate(영향 받는 비중) 를 만들 때 쓰는 rate 를 backend API 에서 조회한다.
조회 키는 seed_id(사업자번호)가 아니라 **upche_cd(업체코드)** + hscode.
DB 의존 없음(httpx only) — nice_shock 의 stateless 원칙 유지.

기본 계약 (backend API 실스펙 확정 전 가정 — 스펙이 오면 이 모듈만 수정):
  GET {RATE_API_URL}?upche_cd=<업체코드>&hscode=<품목코드>
  200 → {"rate": 0.42}   # 0~1 소수점 비율 (범위 밖이면 계약 위반으로 해당 시드 제외)
  404 → 해당 (업체, HS) 거래 없음 → 해당 시드 제외(excluded)

환경변수
  RATE_API_URL     : 필수. 미설정이면 tariff 호출 전체가 503.
  RATE_API_TIMEOUT : 초 (기본 5.0).

개발/시연용 목업: ``nice_shock.mock_rate_api`` (compose 의 rate-mock 서비스) — 위 계약을
그대로 구현한 결정적 가짜 서버. RATE_API_URL 을 목업으로 지정해 사용.

오류 구분
  RateApiUnavailable : 설정 누락·연결 실패·타임아웃·5xx — 서비스 수준 문제 → 503.
  RateLookupFailed   : 404·응답 형식 오류·0~1 범위 위반 — 시드 수준 문제 → excluded.
"""
from __future__ import annotations

import os

import httpx

RATE_API_URL_ENV = "RATE_API_URL"
RATE_API_TIMEOUT_ENV = "RATE_API_TIMEOUT"
_DEFAULT_TIMEOUT = 5.0


class RateApiUnavailable(Exception):
    """rate API 자체에 도달 불가 (미설정/연결/타임아웃/5xx) — 요청 전체 503 대상."""


class RateLookupFailed(Exception):
    """이 (upche_cd, hscode) 의 rate 를 쓸 수 없음 (없음/형식/범위) — 시드 excluded 대상."""


def fetch_rate(upche_cd: str, hscode: str) -> float:
    """(upche_cd, hscode) 의 rate 를 backend API 에서 조회해 0~1 로 검증 후 반환."""
    url = os.environ.get(RATE_API_URL_ENV)
    if not url:
        raise RateApiUnavailable(f"{RATE_API_URL_ENV} 미설정 — backend rate API 주소가 필요합니다")
    timeout = float(os.environ.get(RATE_API_TIMEOUT_ENV, _DEFAULT_TIMEOUT))
    try:
        resp = httpx.get(url, params={"upche_cd": upche_cd, "hscode": hscode}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise RateApiUnavailable(f"rate API 연결 실패: {exc.__class__.__name__}") from exc
    if resp.status_code == 404:
        raise RateLookupFailed(f"(upche_cd={upche_cd}, hscode={hscode}) 거래 없음 (404)")
    if resp.status_code >= 500:
        raise RateApiUnavailable(f"rate API 서버 오류: HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise RateLookupFailed(f"rate 조회 실패: HTTP {resp.status_code}")
    try:
        rate = float(resp.json()["rate"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RateLookupFailed(f"rate 응답 형식 오류: {exc.__class__.__name__}") from exc
    if not 0.0 <= rate <= 1.0:
        raise RateLookupFailed(f"rate 범위 위반 (0~1 필요): {rate}")
    return rate
