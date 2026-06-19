"""쇼크 시나리오 래퍼 기능 보고서 — HTML → PDF(google-chrome --headless).

기능별로 화면(스크린샷)·계산식·내용을 정리한다. 검증 수치는 실제 PG(HS 8481,
시드 10, depth 3) 기준 측정값. RAG 최종보고서와 동일 렌더 방식/스타일.

  python scripts/shock_scenario_report.py
  → docs/SHOCK_SCENARIO_REPORT_<DATE>.{html,pdf}
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

DATE = "20260619"
HTML_PATH = f"docs/SHOCK_SCENARIO_REPORT_{DATE}.html"
PDF_PATH = f"docs/SHOCK_SCENARIO_REPORT_{DATE}.pdf"

# 데모 화면 캡처 (Streamlit 구동 후 playwright 캡처본).
IMG = {
    "overview": "/tmp/shock_flow_overview.png",
    "nodes": "/tmp/shock_zoom_nodes.png",
    "edges": "/tmp/shock_zoom_edges.png",
    "random": "/tmp/random_board.png",
    "random_delta": "/tmp/random_delta.png",
}


def _img(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f'<div class="warn callout">[이미지 없음: {path}]</div>'
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img class="shot" src="data:image/png;base64,{b64}" />'


CSS = """
@page { size: A4; margin: 15mm 13mm; }
* { box-sizing: border-box; }
body { font-family:'NanumGothic','NanumSquare','NanumSquareRound',sans-serif; color:#1a2533; font-size:11px; line-height:1.55; }
h1 { font-size:21px; border-bottom:3px solid #2c5fa8; padding-bottom:8px; color:#1a2533; margin-bottom:4px; }
h2 { font-size:14.5px; color:#2c5fa8; margin-top:24px; border-left:4px solid #2c5fa8; padding-left:8px; }
h3 { font-size:12px; color:#33475b; margin-top:15px; margin-bottom:4px; }
.meta { color:#5a6b7b; font-size:10px; margin-bottom:2px; }
table { border-collapse:collapse; width:100%; margin:8px 0; font-size:10px; }
th,td { border:1px solid #cdd7e1; padding:4px 7px; text-align:left; vertical-align:top; }
th { background:#eef3fa; color:#2c5fa8; font-weight:bold; }
tr:nth-child(even) td { background:#fafbfd; }
.formula { font-family:'D2Coding','Consolas','DejaVu Sans Mono',monospace; background:#f4f7fb; border:1px solid #d6e0ec; border-radius:5px; padding:9px 12px; margin:8px 0; font-size:10.5px; line-height:1.7; white-space:pre-wrap; color:#1a2533; }
.formula b { color:#2c5fa8; }
.callout { background:#eafaf1; border-left:4px solid #27ae60; padding:8px 12px; margin:10px 0; font-size:10px; }
.warn { background:#fef9e7; border-left:4px solid #f39c12; }
.info { background:#eef3fa; border-left:4px solid #2c5fa8; }
.good { color:#1e8449; font-weight:bold; }
.bad { color:#c0392b; font-weight:bold; }
.shot { width:100%; border:1px solid #cdd7e1; border-radius:6px; margin:6px 0; }
.tag { display:inline-block; background:#2c5fa8; color:#fff; border-radius:3px; padding:1px 7px; font-size:9px; margin-right:4px; }
.tag.b { background:#8e44ad; }
.small { font-size:9.5px; color:#5a6b7b; }
.pagebreak { page-break-before:always; }
ul { margin:6px 0 6px 0; padding-left:18px; }
li { margin:2px 0; }
code { background:#eef1f5; border-radius:3px; padding:0 4px; font-family:'D2Coding','Consolas',monospace; font-size:10px; }
"""


def build_html() -> str:
    p: list[str] = []
    a = p.append
    a("<!doctype html><html lang='ko'><head><meta charset='utf-8'>")
    a(f"<style>{CSS}</style></head><body>")

    # ── 표지/개요 ────────────────────────────────────────────────────────────
    a("<h1>외생충격 시나리오 래퍼 — 기능 보고서</h1>")
    a("<div class='meta'>프로젝트: nice-backend · 모듈: <code>nice_graph.shock</code> · 작성일: 2026-06-19</div>")
    a("<div class='meta'>대상: 관세 충격 / 거래 변화 시나리오 래퍼 + <code>/api/shock/scenario</code> + Streamlit 데모</div>")

    a("<h2>0. 개요 — 단일 알고리즘 + 2축 래퍼</h2>")
    a("<p>충격 전파의 실제 계산은 <code>propagate_shock</code>(거듭제곱급수 합) 하나뿐이다. "
      "요구된 4갈래(관세충격×{매출,매입}, 거래변화×{매출,매입})는 새 엔진이 아니라 "
      "<b>두 직교 축의 조합</b>으로 구현된다. 알고리즘은 무변경.</p>")
    a("<table><tr><th>축</th><th>값</th><th>의미</th><th>구현 위치</th></tr>"
      "<tr><td>방향(direction)</td><td>upstream / downstream</td>"
      "<td>상류·매출 파급(가중치 A) / 하류·매입 파급(가중치 B). 엣지 방향 + 정규화 분모 전환</td>"
      "<td><code>assemble.py</code> 인자</td></tr>"
      "<tr><td>시나리오(scenario)</td><td>tariff / transaction_change</td>"
      "<td>W불변·시드주입 / 특정 거래비중 g수정→변화분 Δ</td>"
      "<td><code>scenario.py</code> 래퍼</td></tr></table>")
    a("<div class='callout'><b>래퍼 깊이</b>: 실질 신규 계층은 <code>scenario.py</code> 1겹. "
      "나머지는 기존 <code>assemble_propagation_input</code>에 노브(direction·weight·g) 추가.</div>")

    # ── 파이프라인 + 핵심 계산식 ───────────────────────────────────────────────
    a("<h2>1. 파이프라인 &amp; 핵심 계산식</h2>")
    a("<p>HS코드 → 1차 기업 시드 → depth-3 거래확장 → (방향·시나리오 반영) 전파.</p>")
    a("<table><tr><th>단계</th><th>함수</th><th>역할</th></tr>"
      "<tr><td>1. 시드 선별</td><td><code>select_primary_firms</code></td><td>ra603 거래구성으로 HS 노출 기업 점수화</td></tr>"
      "<tr><td>2. 그래프 조립</td><td><code>assemble_propagation_input</code></td><td>company_edge depth-3 유도부분그래프 → R, init</td></tr>"
      "<tr><td>3. 전파</td><td><code>propagate_shock</code></td><td>거듭제곱급수 합 (active-set 반복)</td></tr>"
      "<tr><td>4. 시나리오</td><td><code>run_tariff_shock</code> / <code>run_transaction_change</code></td><td>방향·가중치·g 조합 묶음</td></tr></table>")

    a("<h3>1.1 전파 엔진</h3>")
    a("<div class='formula'>"
      "<b>total_effect</b> = Σ<sub>k≥0</sub> R<sup>k</sup> · init\n"
      "라운드 갱신:  next_shock[t] += cur_shock[s] · rate(s→t)   (모든 엣지 s→t)\n"
      "종료:  모든 |propagated| ≤ ε(1e-8) → 자연수렴  /  max_iter=500 안전장치"
      "</div>")

    a("<h3>1.2 비중(rate) 정규화 — 방향별</h3>")
    a("<div class='formula'>"
      "α = damping (홉당 감쇠),  W = 방향 가중치(A 또는 B),  amt = sly_amt(셀러→바이어)\n\n"
      "<b>downstream</b> (셀러s→바이어t, 매입 파급, W=B):\n"
      "   rate(s→t) = B · α · amt(s→t) / Σ<sub>t'</sub> amt(s→t')      [분모=셀러 s 의 총매출]\n\n"
      "<b>upstream</b> (바이어t→셀러s, 매출 파급, W=A):\n"
      "   rate(t→s) = A · α · amt(s→t) / Σ<sub>s'</sub> amt(s'→t)      [분모=바이어 t 의 총매입]"
      "</div>")
    a("<div class='callout'><b>수렴 불변식</b>: within_subgraph 정규화로 각 source 의 "
      "Σ<sub>out</sub> = W·α ≤ 1 → ρ(R) ≤ W·α &lt; 1 → 거듭제곱급수 절대수렴. "
      "방향을 뒤집을 때 <b>분모 PARTITION 도 새 source 기준으로 전환</b>하는 것이 핵심.</div>")

    a("<h3>1.3 정규화 기준 옵션 (normalize) — 방향과 직교</h3>")
    a("<p>분모를 어느 끝 기준으로 잡는가를 <b>방향과 분리</b>해 선택. 수렴 보장과 경제적 명칭 "
      "충실 사이를 옵션으로 제공.</p>")
    a("<table><tr><th>normalize</th><th>분모 기준</th><th>downstream(매입)</th><th>upstream(매출)</th><th>수렴</th></tr>"
      "<tr><td><b>source</b> (기본)</td><td>전파 source(orientation 출발)</td><td>셀러 총매출</td><td>바이어 총매입</td>"
      "<td>Σ_out=W·α≤1 → <b>절대수렴 보장</b></td></tr>"
      "<tr><td><b>counterparty</b></td><td>거래상대(orientation 도착)</td><td>바이어 총매입</td><td>셀러 총매출</td>"
      "<td>경제적 매출/매입 비중 라벨 충실, 단 Σ_out 무제한 → <b>수렴 보장 약화</b>(damping 의존·발산 시 converged=False)</td></tr></table>")
    a("<div class='callout'><b>쌍대(dual) 불변식 검증</b>: source 모드는 각 source 의 Σ_out=0.85, "
      "counterparty 모드는 각 <b>target 의 Σ_in=0.85</b> 가 정확히 성립(실 PG, 위반 0). "
      "rate 는 745/747 엣지에서 달라져 옵션이 실제로 계산을 바꿈을 확인.</div>")

    # ── 기능 2: 관세 충격 ──────────────────────────────────────────────────────
    a("<h2 class='pagebreak'>2. 기능 — 관세 충격 (tariff)</h2>")
    a("<p><span class='tag'>W 불변</span> 그래프 구조는 그대로, 1차 기업(시드)에 외생 충격만 주입. "
      "한 번 호출로 <b>매출 파급(상류, A)</b>과 <b>매입 파급(하류, B)</b>을 동시 산출.</p>")
    a("<h3>계산식</h3>")
    a("<div class='formula'>"
      "init = { seed_node : shock }   (shock = score 비례 또는 균등)\n"
      "매출 파급:  result_A = Σ R<sub>upstream</sub><sup>k</sup> · init    (rate = A·α·매출비중)\n"
      "매입 파급:  result_B = Σ R<sub>downstream</sub><sup>k</sup> · init  (rate = B·α·매입비중)"
      "</div>")
    a("<h3>내용 — 방향 ↔ 라벨 ↔ 가중치</h3>")
    a("<table><tr><th>direction</th><th>엣지 방향</th><th>파급 효과</th><th>가중치</th><th>정규화 분모</th></tr>"
      "<tr><td>upstream</td><td>바이어→셀러</td><td>매출 파급(상류)</td><td>A</td><td>바이어 총매입</td></tr>"
      "<tr><td>downstream</td><td>셀러→바이어</td><td>매입 파급(하류)</td><td>B</td><td>셀러 총매출</td></tr></table>")
    a("<h3>화면 — 시드 추출 → 시나리오 전파 (방향별 탭)</h3>")
    a(_img(IMG["overview"]))
    a("<div class='small'>HS 8481 → 1차 기업 10곳 → depth-3 (노드 321·엣지 747). "
      "하단에 '매출 파급(upstream)' / '매입 파급(downstream)' 탭이 생성된다.</div>")

    a("<h3>화면 — 노드 그리드 (값 반영 결과)</h3>")
    a(_img(IMG["nodes"]))
    a("<div class='small'>node_id(복합키) · bizno · upchecd · 기업명 · 시드 · seed_shock · shock. "
      "shock 내림차순. 시드(Y)는 초기충격(seed_shock)을 받아 상위.</div>")

    a("<h3>화면 — 엣지 그리드 (rate = 방향·A/B·g 반영)</h3>")
    a(_img(IMG["edges"]))
    a("<div class='small'>from · to(복합키) · 양끝 기업명 · rate. "
      "rate 는 방향·가중치·g 가 모두 곱해진 <b>전파에 실제 투입된 값</b>이라, 래퍼 효과를 직접 대조 가능.</div>")

    # ── 기능 3: 거래 변화 ──────────────────────────────────────────────────────
    a("<h2 class='pagebreak'>3. 기능 — 거래 변화 (transaction_change)</h2>")
    a("<p><span class='tag b'>W 수정</span> 특정 1차→2차(셀러→바이어) 거래의 비중에 0~1 인자 g 를 곱한 "
      "<b>수정 그래프</b>로 전파하고, 원본 대비 <b>변화분 Δ</b>를 산출.</p>")
    a("<h3>계산식 — difference-of-runs</h3>")
    a("<div class='formula'>"
      "rate'(s→t) = g · rate(s→t)    for (s,t) ∈ overrides,  else rate(s→t)\n"
      "baseline = Σ R<sup>k</sup> · init        (원 W)\n"
      "changed  = Σ R'<sup>k</sup> · init       (수정 W)\n"
      "<b>변화분 Δ(node)</b> = changed(node) − baseline(node)"
      "</div>")
    a("<h3>내용</h3>")
    a("<ul>"
      "<li>오버라이드 키 = 저장방향 <code>(셀러_bizno, 바이어_bizno)</code>. 방향(상류/하류) 무관하게 원 거래쌍으로 매칭.</li>"
      "<li>g&lt;1 = 거래 비중 축소 → 해당 거래 하류 전파 감소 → 대상 바이어 및 그 하류 노드 Δ&lt;0.</li>"
      "<li>Δ 는 음수 가능(축소). 데모 그래프는 |Δ| 기준으로 노드 크기·순위 표시.</li></ul>")
    a("<div class='callout'><b>실측 예</b> (override 1438123482→3148200884, g=0.5, downstream): "
      "변화 발생 노드 130곳 중 <b>128곳 감소(Δ&lt;0)</b>, 대상 바이어 Δ=−0.0199. "
      "부호·방향이 경제 직관과 일치.</div>")

    # 3.1 — 1차↔2차 매출/매입 랜덤
    a("<h3>3.1 1차↔2차 매출/매입 랜덤 가중치 (신규)</h3>")
    a("<p>특정 HS 에 연계된 <b>1차 기업</b>이 <b>2차 기업</b>과 맺은 거래의 매출/매입에 "
      "<b>랜덤 g</b> 를 자동 부여하는 시나리오. 엣지를 일일이 지정하지 않고 한 번에 생성하며, "
      "<b>난수 시드</b>로 재현 가능.</p>")
    a("<table><tr><th>분류</th><th>거래 방향 (저장: 셀러→바이어)</th><th>대상 엣지</th></tr>"
      "<tr><td>매출(sales)</td><td>1차(셀러) → 2차(바이어)</td><td>sb∈1차, bb∉1차</td></tr>"
      "<tr><td>매입(purchase)</td><td>2차(셀러) → 1차(바이어)</td><td>bb∈1차, sb∉1차</td></tr></table>")
    a("<div class='formula'>"
      "후보 = { (s,t) ∈ 1차↔2차 엣지 :  side 가 sales→매출만 / purchase→매입만 / both→둘 다 }\n"
      "정렬(후보) 후  g(s,t) = Uniform(low, high)  with seed   (DB 행순서 무관·재현 보장)\n"
      "1차↔1차·2차↔3차 (양끝 동시 1차 또는 1차 미포함) 는 제외"
      "</div>")
    a("<ul>"
      "<li><b>side</b>: both(매출+매입) / sales(매출만) / purchase(매입만)</li>"
      "<li><b>범위 [low, high]</b> ⊆ [0,1] (상한 1 → Σ_out≤W·α 유지, 수렴 보장)</li>"
      "<li><b>seed</b>: 재현용 난수 시드 / <b>only_firms</b>: 일부 1차 기업만 한정(비우면 전체)</li></ul>")
    a("<div class='info callout'><b>API</b>: <code>POST /api/shock/scenario</code> 에 "
      "<code>random_override:{side,low,high,seed,only_firms}</code> 추가. 지정 시 서버가 "
      "1차↔2차 매출/매입 랜덤 g 를 생성하고, 응답 <code>applied_overrides</code> 로 실제 적용값을 반환(재현·표시용).</div>")
    a("<h3>화면 — 거래 변화 보드 (랜덤 모드)</h3>")
    a(_img(IMG["random"]))
    a("<div class='small'>대상 거래(매출/매입/둘다)·g 범위·재현 시드·대상 1차 선택 → "
      "‘랜덤 가중치 생성’ → 구분(매출/매입)·셀러·바이어·g 그리드. "
      "실측: HS 8481·seed 42 → 40건(매출 24·매입 16).</div>")
    a("<h3>화면 — 변화분 Δ 결과 (랜덤 적용 후)</h3>")
    a(_img(IMG["random_delta"]))
    a("<div class='small'>매출(상류)/매입(하류) 방향별 탭 · 변화분Δ 그리드(음수=거래축소로 인한 파급 감소) · CSV. "
      "실측 Σ변화분Δ=−15.898 (매출 방향, 수렴 104회).</div>")

    # ── 엔드포인트 ────────────────────────────────────────────────────────────
    a("<h2>4. 엔드포인트 — <code>POST /api/shock/scenario</code></h2>")
    a("<h3>요청 (주요 필드)</h3>")
    a("<table><tr><th>필드</th><th>타입</th><th>설명</th></tr>"
      "<tr><td>scenario</td><td>tariff | transaction_change</td><td>시나리오 종류</td></tr>"
      "<tr><td>seeds</td><td>[{bizno, upchecd, shock}]</td><td>1차 기업 + 초기충격</td></tr>"
      "<tr><td>directions</td><td>[upstream, downstream]</td><td>계산 방향(기본 둘 다)</td></tr>"
      "<tr><td>weight_a / weight_b</td><td>float (&gt;0)</td><td>상류(매출) / 하류(매입) 가중치</td></tr>"
      "<tr><td>depth, damping, within_subgraph, trade_year</td><td>—</td><td>조립/전파 파라미터</td></tr>"
      "<tr><td>normalize</td><td>source | counterparty</td><td>분모 기준 — source(수렴보장) / counterparty(매출·매입 비중 라벨)</td></tr>"
      "<tr><td>edge_overrides</td><td>[{from_bizno, to_bizno, factor}]</td><td>거래변화 전용 — 명시 g (factor, 0~1)</td></tr>"
      "<tr><td>random_override</td><td>{side, low, high, seed, only_firms}</td><td>거래변화 전용 — 1차↔2차 매출/매입 랜덤 g 자동생성</td></tr></table>")
    a("<h3>응답</h3>")
    a("<div class='formula'>"
      "{ scenario, warnings[], applied_overrides:[{from_bizno, to_bizno, factor}],\n"
      "  directions: [ { direction, effect_label, weight,\n"
      "                  shock_list:[{bizno, shock}], total_shock, iterations, converged,\n"
      "                  n_nodes, n_edges } ] }"
      "</div>")
    a("<div class='small'>applied_overrides = 실제 적용된 거래변화 g(랜덤 생성 포함) — 재현·표시용.</div>")
    a("<div class='small'>tariff=각 노드 누적 파급 / transaction_change=노드별 변화분 Δ. "
      "transaction_change 에 edge_overrides 가 비면 422.</div>")

    # ── 검증 ──────────────────────────────────────────────────────────────────
    a("<h2 class='pagebreak'>5. 기능 검증 결과</h2>")
    a("<p>자동화 테스트 <b>64개 전부 통과</b>(propagate 18 + 라우터 15 + 시나리오 31). "
      "추가로 실제 PG 기반 수학 불변식·HTTP 응답을 교차검증.</p>")
    a("<h3>5.1 수학 불변식 (실 PG · HS 8481 · 노드 321 · 엣지 747)</h3>")
    rows = [
        ("downstream 각 source Σ_out == 0.85", "PASS", "위반 0 · 정규화 분모 정확"),
        ("upstream 각 source Σ_out == 0.85", "PASS", "위반 0 · 방향 전환 후 재정규화 정확"),
        ("노드 집합 downstream == upstream", "PASS", "차집합 0 (321=321)"),
        ("엣지 방향 뒤집힘 (down == reversed up)", "PASS", "불일치 0 (747=747)"),
        ("direction_weight=0.5 → rate 정확히 절반", "PASS", "A/B 선형 반영"),
        ("override g=0.4 → 대상 엣지만 ×0.4", "PASS", "0.0159→0.0064"),
        ("override 나머지 엣지 불변", "PASS", "부작용 없음"),
        ("Δ == 수동(changed − baseline)", "PASS", "difference-of-runs 정확"),
        ("거래비중 축소 → 하류 Δ&lt;0", "PASS", "대상 바이어 Δ=−0.0199"),
        ("tariff upstream 수렴", "PASS", "104회 · Σ=32.08"),
        ("tariff downstream 수렴", "PASS", "103회 · Σ=43.01"),
        ("랜덤 override 모두 1차↔2차", "PASS", "매출 24·매입 16·위반 0"),
        ("랜덤 seed 재현성", "PASS", "동일 seed→동일 40건"),
        ("side=sales → 매입 0 / only_firms 한정", "PASS", "필터 정확"),
        ("랜덤 거래변화 전파 수렴·감소우세", "PASS", "128/128 Δ&lt;0 · Σ=−19.07"),
        ("normalize=source: source Σ_out=0.85", "PASS", "위반 0"),
        ("normalize=counterparty: target Σ_in=0.85(dual)", "PASS", "위반 0 · rate 745/747 변경"),
    ]
    a("<table><tr><th>불변식</th><th>결과</th><th>측정</th></tr>")
    for name, res, extra in rows:
        a(f"<tr><td>{name}</td><td class='good'>{res}</td><td>{extra}</td></tr>")
    a("</table>")

    a("<h3>5.2 HTTP 엔드포인트 (실 DB)</h3>")
    a("<table><tr><th>검증</th><th>결과</th></tr>"
      "<tr><td>tariff 200 + 양방향(매출 A=0.8/매입 B=0.6) 구조·라벨·수렴</td><td class='good'>PASS</td></tr>"
      "<tr><td>transaction_change 200 + Δ(130노드 중 128 감소, Σ=−0.67)</td><td class='good'>PASS</td></tr>"
      "<tr><td>transaction_change 빈 overrides → 422</td><td class='good'>PASS</td></tr>"
      "<tr><td>random_override 경로 → 생성·전달·applied_overrides 직렬화</td><td class='good'>PASS</td></tr>"
      "<tr><td>OpenAPI 경로 등록</td><td class='good'>PASS</td></tr></table>")
    a("<div class='callout'><b>종합</b>: 기능 정상 동작. 렌더링뿐 아니라 방향 전환·정규화·가중치·"
      "거래변화 Δ의 수치 정확성까지 확인됨.</div>")

    # ── 미확정 ────────────────────────────────────────────────────────────────
    a("<h2>6. 확정 대기 — 도메인 의미 (기능 버그 아님, 정의 선택)</h2>")
    a("<ul>"
      "<li><b>정규화 ↔ 라벨</b>: 수렴 보장을 위해 상류(매출 파급)를 '바이어 총매입' 분모로 정규화함. "
      "경제적으로 '셀러 총매출' 분모가 맞다면 <code>assemble</code>의 <code>src_col</code>만 전환(엔진 무변경).</li>"
      "<li><b>변화분 정의</b>: difference-of-runs(수정W−원W)로 구현. '델타를 seed로 직접 주입'이 본 의도면 해당 부분만 교체.</li></ul>")

    a("</body></html>")
    return "".join(p)


def main() -> int:
    Path("docs").mkdir(exist_ok=True)
    html_str = build_html()
    Path(HTML_PATH).write_text(html_str, encoding="utf-8")
    print(f"HTML 작성: {HTML_PATH}")
    r = subprocess.run(
        ["google-chrome", "--headless", "--no-sandbox", "--disable-gpu",
         f"--print-to-pdf={PDF_PATH}", "--no-pdf-header-footer",
         "--print-to-pdf-no-header", HTML_PATH],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("PDF 렌더 실패:", r.stderr[-500:], file=sys.stderr)
        return 1
    print(f"PDF 작성: {PDF_PATH} (rc={r.returncode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
