"""최종 보고서 렌더 — /tmp/final_eval.json → docs/RAG_FINAL_REPORT_<date>.{html,pdf}.

HTML 은 포맷 수정용으로 영구 보존(docs/). PDF 는 google-chrome --headless 로 렌더.
한글 폰트 NanumGothic 시스템 설치 전제.
"""

from __future__ import annotations

import html
import json
import subprocess
import sys

DATE = "20260618"
JSON_PATH = "/tmp/final_eval.json"
REPORT_DIR = "docs/reports/rag"
HTML_PATH = f"{REPORT_DIR}/RAG_FINAL_REPORT_{DATE}.html"
PDF_PATH = f"{REPORT_DIR}/RAG_FINAL_REPORT_{DATE}.pdf"

# v2(2026-06-12, RAG_RAGAS_EXTENDED) 기준값 — 재실행과 비교용
V2 = {"hit@1": 0.650, "hit@5": 0.950, "hit@10": 1.000, "mrr": 0.777}

CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: 'NanumGothic','Nanum Gothic',sans-serif; color:#1a2533; font-size:11px; line-height:1.5; }
h1 { font-size:20px; border-bottom:3px solid #2c5fa8; padding-bottom:8px; color:#1a2533; }
h2 { font-size:14px; color:#2c5fa8; margin-top:22px; border-left:4px solid #2c5fa8; padding-left:8px; }
h3 { font-size:12px; color:#33475b; margin-top:14px; }
.meta { color:#5a6b7b; font-size:10px; margin-bottom:4px; }
table { border-collapse:collapse; width:100%; margin:8px 0; font-size:10px; }
th,td { border:1px solid #cdd7e1; padding:4px 6px; text-align:left; vertical-align:top; }
th { background:#eef3fa; color:#2c5fa8; font-weight:bold; }
tr:nth-child(even) td { background:#fafbfd; }
.metric-row { display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; }
.metric { flex:1; min-width:110px; background:#eef3fa; border:1px solid #cdd7e1; border-radius:6px; padding:10px; text-align:center; }
.metric .v { font-size:20px; font-weight:bold; color:#2c5fa8; }
.metric .l { font-size:9px; color:#5a6b7b; margin-top:3px; }
.callout { background:#eafaf1; border-left:4px solid #27ae60; padding:8px 12px; margin:10px 0; font-size:10px; }
.warn { background:#fef9e7; border-left:4px solid #f39c12; }
.good { color:#1e8449; font-weight:bold; }
.bad { color:#c0392b; font-weight:bold; }
.mono { font-family:'D2Coding',monospace; }
.delta-pos { color:#1e8449; } .delta-neg { color:#c0392b; }
.small { font-size:9px; color:#5a6b7b; }
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def pct(x: float) -> str:
    return f"{x*100:.1f}%"


def delta(now: float, ref: float, is_pct=True) -> str:
    d = now - ref
    cls = "delta-pos" if d >= 0 else "delta-neg"
    s = f"{d*100:+.1f}%p" if is_pct else f"{d:+.3f}"
    return f'<span class="{cls}">{s}</span>'


def build(data: dict) -> str:
    ov = data["overall"]
    by_tmpl = data["by_tmpl"]
    by_seed = data["by_seed"]
    rows = data["rows"]
    scen = data["scenario"]

    P = []
    P.append(f"<h1>HSK RAG 자연어 질의 최종 평가 보고서 ({len(rows)}문항 + 시나리오 {len(scen)})</h1>")
    P.append('<div class="meta">작성일 2026-06-18 · 대상 nice_rag (RRF 하이브리드 + LLM 품목추출 + agent) · 라이브 rag-server 재실행</div>')
    P.append('<div class="meta">방법론 RAGAS(검색 랭킹: 기대 prefix rank 매칭으로 hit@k·MRR). 자연어 120문항 = 20시드 × 6템플릿. 시나리오 13문항 = 공급망 6 + 비관련 5 + 추가검증 2.</div>')

    # 1. 종합 지표
    P.append("<h2>1. 종합 지표 (자연어 120문항, 재실행)</h2>")
    P.append('<div class="metric-row">')
    for label, key in [("hit@1", "hit@1"), ("hit@5", "hit@5"), ("hit@10", "hit@10")]:
        P.append(f'<div class="metric"><div class="v">{pct(ov[key])}</div><div class="l">{label}</div></div>')
    P.append(f'<div class="metric"><div class="v">{ov["mrr"]:.3f}</div><div class="l">MRR</div></div>')
    P.append(f'<div class="metric"><div class="v">{ov["found"]}/{ov["n"]}</div><div class="l">top10 발견</div></div>')
    P.append("</div>")
    P.append('<table><tr><th>지표</th><th>v2 기준 (2026-06-12)</th><th>재실행 (2026-06-18)</th><th>Δ</th></tr>')
    for label, key, isp in [("hit@1", "hit@1", True), ("hit@5", "hit@5", True), ("hit@10", "hit@10", True), ("MRR", "mrr", False)]:
        ref, now = V2[key], ov[key]
        refs = pct(ref) if isp else f"{ref:.3f}"
        nows = pct(now) if isp else f"{now:.3f}"
        P.append(f"<tr><td>{label}</td><td>{refs}</td><td><b>{nows}</b></td><td>{delta(now, ref, isp)}</td></tr>")
    P.append("</table>")
    P.append(f'<div class="small">latency p50 ~{data.get("latency_p50_s",0):.1f}s (문장형 LLM추출 포함, CPU) · 120문항 wall {data.get("wall120_s",0):.0f}s</div>')

    # 2. 템플릿 변형별
    P.append("<h2>2. 템플릿 변형별 분해 (6종)</h2>")
    P.append('<table><tr><th>변형</th><th>n</th><th>hit@1</th><th>hit@5</th><th>hit@10</th><th>MRR</th><th>발견</th></tr>')
    names = {"keyword": "키워드형(추출 미적용)", "import_q": "수입 질문형", "tariff_q": "관세율 질문형",
             "country_declare": "국가+신고형", "hs_wonder": "HS부호 궁금형", "export_sebun": "수출+세번형"}
    for t in ["keyword", "import_q", "tariff_q", "country_declare", "hs_wonder", "export_sebun"]:
        m = by_tmpl[t]
        P.append(f"<tr><td>{names[t]}</td><td>{m['n']}</td><td>{pct(m['hit@1'])}</td><td>{pct(m['hit@5'])}</td>"
                 f"<td>{pct(m['hit@10'])}</td><td>{m['mrr']:.3f}</td><td>{m['found']}/{m['n']}</td></tr>")
    P.append("</table>")

    # 3. 시드별 MRR
    P.append("<h2>3. 시드 품목별 MRR (낮은 순)</h2>")
    P.append('<table><tr><th>시드 품목</th><th>MRR (6변형 평균)</th></tr>')
    for name, mrr in sorted(by_seed.items(), key=lambda kv: kv[1]):
        P.append(f"<tr><td>{esc(name)}</td><td>{mrr:.3f}</td></tr>")
    P.append("</table>")

    # 4~6. 시나리오
    def scen_table(cat: str, title: str, note: str = ""):
        items = [s for s in scen if s["cat"] == cat]
        P.append(f"<h2>{title} ({len(items)}문항)</h2>")
        if note:
            P.append(f'<div class="small">{note}</div>')
        P.append('<table><tr><th>질의</th><th>/search top3 (score)</th><th>/agent 답변 (요약)</th><th>판정</th></tr>')
        for s in items:
            top = "<br>".join(f'<span class="mono">{esc(t["hs_code"])}</span> {t["score"]} {esc((t["name_ko"] or "")[:14])}' for t in s["top3"]) or "<i>(빈 리스트 — 추천 불가)</i>"
            ans = esc(s["answer"][:160]) if s["answer"] else ""
            vc = "good" if "유효" in s["verdict"] or "정상" in s["verdict"] else ("bad" if "오추천" in s["verdict"] else "")
            P.append(f'<tr><td>{esc(s["q"])}</td><td>{top}</td><td class="small">{ans}</td><td class="{vc}">{esc(s["verdict"])}</td></tr>')
        P.append("</table>")

    scen_table("supply", "4. 공급망 시나리오")
    scen_table("unrelated", "5. 비관련 일반 질의", "기대 동작: /search 빈 리스트 + /agent 명시 거부 (2중 차단).")
    scen_table("extra", "6. 추가 검증 질의", "규격 수치 보존(브릭스) · CRAG 런타임 보정(원유) 확인용.")

    # 7. 전체 테스트셋 120
    P.append("<h2>7. 테스트셋 전체 결과 (120문항)</h2>")
    P.append('<div class="small">rank = /search top10 에서 기대 prefix 가 처음 등장한 순위 (—=top10 밖). 변형 6종을 시드별로 묶어 표기.</div>')
    P.append('<table><tr><th>시드</th><th>기대 prefix</th>' + "".join(f"<th>{n}</th>" for n in ["kw", "수입", "관세", "신고", "HS", "수출"]) + '</tr>')
    order = ["keyword", "import_q", "tariff_q", "country_declare", "hs_wonder", "export_sebun"]
    by_id = {r["id"]: r for r in rows}
    for idx, (name, prefixes) in enumerate([(r["seed"], r.get("prefixes", [])) for r in rows if r["tmpl"] == "keyword"]):
        cells = []
        for t in order:
            r = by_id.get(f"{idx:02d}-{t}")
            rk = r["rank"] if r else None
            cells.append(f'<td>{rk if rk is not None else "—"}</td>')
        pref = ",".join(prefixes)
        P.append(f'<tr><td>{esc(name)}</td><td class="mono">{esc(pref)}</td>{"".join(cells)}</tr>')
    P.append("</table>")

    # 8. 미발견 분석
    miss = [r for r in rows if r["rank"] is None]
    refusals = [r for r in miss if not r.get("top1")]
    retr = [r for r in miss if r.get("top1")]
    P.append(f"<h2>8. 미발견 {len(miss)}건 분석 (거부 {len(refusals)} / 검색실패 {len(retr)})</h2>")
    P.append('<div class="small">거부 = /search CRAG 가 빈 리스트(추천 불가) 반환 — 정답 점수가 CRAG 임계(0.033) 경계. '
             '검색실패 = 후보는 반환했으나 정답이 top-10 밖.</div>')
    P.append('<table><tr><th>ID</th><th>시드</th><th>기대 prefix</th><th>top1 (실제 1위)</th><th>분류</th></tr>')
    for r in sorted(miss, key=lambda x: x["id"]):
        is_ref = not r.get("top1")
        if is_ref:
            top1 = "<i>(빈 리스트 — 추천 불가)</i>"
            label = "거부 (CRAG 빈 리스트)"
        else:
            top1 = f'<span class="mono">{esc(r["top1"])}</span> {esc((r.get("top1_name") or "")[:14])}'
            label = "검색실패 (top10 밖)"
        P.append(f'<tr><td class="mono">{esc(r["id"])}</td><td>{esc(r["seed"])}</td>'
                 f'<td class="mono">{esc(",".join(r.get("prefixes", [])))}</td><td>{top1}</td>'
                 f'<td class="bad">{esc(label)}</td></tr>')
    P.append("</table>")
    P.append('<div class="callout warn">처방 분리 — <b>거부</b>: CRAG 임계(0.033)와 정답 점수(예 LNG 271111 = 0.0328)가 겹치는 '
             '경계 문제로, 임계 하향 또는 동의어로 점수 상향 시 해소. <b>검색실패</b>: 색인 커버리지/임베딩 문제(예 불화수소산 '
             '281111 이 ○○수소산류보다 하위) — reranker·색인 보강(본 사업) 영역.</div>')

    P.append('<div class="callout">방법: 라이브 rag-server <span class="mono">/api/hsk/search</span>(limit=10)·'
             '<span class="mono">/api/hsk/agent</span>(k=5) 실제 호출. hit@k=기대 prefix 가 top-k 내, MRR=1/rank. '
             'faithfulness·answer_relevancy 등 RAGAS 생성지표는 별도 judge 하니스 영역으로 본 재실행에는 검색 랭킹 지표만 포함.</div>')

    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(P)}</body></html>"


def main() -> None:
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    html_doc = build(data)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html_doc)
    print(f"HTML 저장: {HTML_PATH}")

    r = subprocess.run(
        ["google-chrome", "--headless", "--no-sandbox", "--disable-gpu",
         f"--print-to-pdf={PDF_PATH}", "--no-pdf-header-footer",
         "--print-to-pdf-no-header", HTML_PATH],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        print("chrome stderr:", r.stderr[-500:], file=sys.stderr)
    print(f"PDF 저장: {PDF_PATH} (rc={r.returncode})")


if __name__ == "__main__":
    main()
