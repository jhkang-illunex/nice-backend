# 보안성 검증 자료 — 에어갭 반입 Docker 이미지 (SBOM + 취약점 스캔)

- **작성일**: 2026-08-04
- **작성자**: jhkang (illunex), Claude Code 지원
- **대상**: `docker-compose.deploy.yml` 기반 에어갭 반입 번들에 포함된 전체 이미지 8종
- **도구**: [Trivy](https://trivy.dev) v0.73.0 (Aqua Security, OSS) — SBOM 생성 + OS/언어 패키지 취약점 스캔을 단일 도구로 수행
- **취약점 DB**: `mirror.gcr.io/aquasec/trivy-db:2`, DB version 2, UpdatedAt 2026-08-04 01:17:42 UTC (스캔 시점 최신)
- **SBOM 형식**: CycloneDX 1.x (JSON)
- **원본 자료 경로**: 본 문서와 같은 폴더의 `sbom/*.cdx.json`(SBOM), `scans/*.json`(Trivy 원본 스캔 결과, 상세 CVE 전체 목록 포함), `scan_summary.json`(심각도별 집계 + CRITICAL/HIGH 요약을 이 문서 작성을 위해 추출한 중간 산출물)

> 본 문서는 "자율 양식" 제출 요건에 맞춘 요약 보고서다. 제출처가 특정 SBOM 표준(SPDX 등)이나
> 특정 스캐너 지정을 요구하면 그에 맞춰 재생성 가능 — Trivy 는 `--format spdx-json` 도 지원한다.

---

## 1. 검증 대상 이미지 목록

| # | 이미지:태그 | Image ID(sha256, 앞 12자) | 크기 | 용도 / 반입 번들 |
|---|---|---|---|---|
| 1 | `nice/migrate:dev` | `cf9bb6ab0296` | 376MB | company_edge rate/공유율 갱신 CLI + IPython 쉘. `nice_migrate.image.tar.gz` |
| 2 | `nice/migrate2:dev` | `cf9bb6ab0296` | 376MB | 위와 **완전 동일 이미지**(같은 Image ID) — 별도 이름으로 재반입한 사본. `nice_migrate2.image.tar.gz` |
| 3 | `nice/rag-server:dev` | `74513ae61d26` | 537MB | HS코드 검색 RAG API. `nice_ai_app.tar.gz` |
| 4 | `nice/ingestion:dev` | `ea725b25e1d4` | 539MB | 데이터 적재/임베딩 배치 잡. `nice_ai_app.tar.gz` |
| 5 | `nice/shock-server:dev` | `b4085be26c6f` | 263MB | 쇼크 전파 계산 API(DB-free). `nice_ai_app.tar.gz` |
| 6 | `nice/postgres:pg16` | `17ee2618c5f6` | 438MB | 자체 호스팅 PostgreSQL(pgvector/pg_trgm/btree_gin). 별도 빌드·반입 |
| 7 | `ollama/ollama:latest` | `dd1a385ce665` | 4.85GB | LLM 서빙(qwen3:14b 등). `nice_ai_llm.tar.gz` |
| 8 | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.6` | `011f81cf9cf6` | 666MB | 임베딩 서빙(bge-m3). `nice_ai_embed.tar.gz` |

> **#1·#2 는 같은 빌드**(`deploy/migrate/Dockerfile`, 동일 커밋)라 스캔 결과가 완전히 동일하다.
> 아래 요약·상세는 중복 게재를 피하기 위해 `nice_migrate` 하나로 대표한다(원본 파일은 둘 다 보존).

---

## 2. 심각도별 요약

| 이미지 | Base OS | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---:|---:|---:|---:|
| nice/migrate (=migrate2) | Debian 13.6 (trixie) | 4 | 61 | 111 | 113 |
| nice/rag-server | Debian 13.6 (trixie) | 4 | 27 | 81 | 101 |
| nice/ingestion | Debian 13.6 (trixie) | 4 | 27 | 81 | 101 |
| nice/shock-server | Debian 13.5 (trixie) | 4 | 27 | 82 | 101 |
| nice/postgres:pg16 | Debian 12.13 (bookworm) | 22 | 59 | 164 | 179 |
| ollama/ollama:latest | (멀티스테이지, distroless 계열) | **0** | 33 | 78 | 30 |
| text-embeddings-inference:cpu-1.6 | Debian 12.10 (bookworm) | 12 | 83 | 161 | 218 |

- `nice/migrate*`·`rag-server`·`ingestion`·`shock-server` 는 모두 같은 `python:3.11-slim`(Debian 13 trixie)
  베이스라 CRITICAL 4건이 공통으로 나타난다(§3-A).
- `ollama/ollama:latest` 는 이번 스캔에서 CRITICAL 0건 — 가장 양호.
- `nice/postgres:pg16`(Debian 12 bookworm) 과 `text-embeddings-inference:cpu-1.6` 은 상대적으로
  오래된 bookworm 베이스라 CRITICAL/HIGH 건수가 더 많다(§3-B, §3-C).

---

## 3. CRITICAL 상세 및 조치 가능 여부

### 3-A. `nice/migrate` / `rag-server` / `ingestion` / `shock-server` 공통 (Debian 13 trixie 베이스)

| CVE | 패키지 | 설치버전 | 수정버전 | 비고 |
|---|---|---|---|---|
| CVE-2026-13221 | perl-base | 5.40.1-6 | **미정(업스트림 패치 대기)** | |
| CVE-2026-42496 | perl-base | 5.40.1-6 | 미정 | |
| CVE-2026-57433 | perl-base | 5.40.1-6 | 미정 | |
| CVE-2026-8376  | perl-base | 5.40.1-6 | 미정 | |

**평가**: 4건 전부 `perl-base`(Debian 베이스 이미지에 기본 포함되는 패키지) 한 곳에 몰려 있고,
현재(스캔 시점) 업스트림 수정 버전이 아직 없다. `nice_migrate`/`rag-server`/`ingestion`/`shock-server`
컨테이너는 애플리케이션 코드가 **perl 을 호출하지 않는다**(Python 전용 CLI/API) — 즉 이 패키지가
런타임 공격표면에 실제로 노출되지는 않으나, 이미지에 존재 자체는 사실이므로 투명하게 명시한다.
업스트림 패치가 나오면 베이스 이미지 재빌드(`docker build`)만으로 자동 해소된다(코드 변경 불필요).

### 3-B. `nice/postgres:pg16` (22건, Debian 12 bookworm)

주요 항목(전체 목록은 `scans/nice_postgres_pg16.json` 참고):

| CVE | 패키지 | 설치버전 | 수정버전 | 조치 가능? |
|---|---|---|---|---|
| CVE-2026-33845, CVE-2026-42010 | libgnutls30 | 3.7.9-2+deb12u6 | **3.7.9-2+deb12u7** | [해소가능] apt 업그레이드로 해소 가능 |
| CVE-2025-68121 | stdlib(Go, `gosu` 바이너리 내장) | v1.24.6 | 1.24.13/1.25.7/1.26.0-rc.3 | [해소가능] `gosu` 재빌드(최신 Go 로 컴파일)로 해소 가능 |
| CVE-2026-13221 등 4건 | libperl5.36/perl/perl-base/perl-modules | 5.36.0-7+deb12u3 | 미정 | [주의] 업스트림 대기(§3-A 와 동일 성격) |
| CVE-2025-7458 | libsqlite3-0 | 3.40.1-2+deb12u2 | 미정 | [주의] 업스트림 대기 |
| CVE-2026-6653 | libxml2 | 2.9.14+dfsg-1.3~deb12u5 | 미정 | [주의] 업스트림 대기 |
| CVE-2023-45853 | zlib1g | 1:1.2.13.dfsg-1 | 미정(Debian 자체 미출시) | [주의] 업스트림 대기 |

**권고**: `deploy/postgres/Dockerfile` 재빌드 시 `apt-get upgrade`(또는 베이스 이미지 재당김)로
`libgnutls30` 최소 1건은 즉시 해소 가능. `gosu` 는 최신 릴리스로 교체 권고.

### 3-C. `text-embeddings-inference:cpu-1.6` (12건, Debian 12 bookworm)

| CVE | 패키지 | 설치버전 | 수정버전 | 조치 가능? |
|---|---|---|---|---|
| CVE-2026-33845, CVE-2026-42010 | libgnutls30 | 3.7.9-2+deb12u4 | 3.7.9-2+deb12u7 | [해소가능] |
| CVE-2026-31789 | libssl3/libssl-dev/openssl | 3.0.15-1~deb12u1 | 3.0.19-1~deb12u2 | [해소가능] |
| CVE-2024-56171 | libxml2 | 2.9.14+dfsg-1.3~deb12u1 | 2.9.14+dfsg-1.3~deb12u2 | [해소가능] |
| 나머지 5건(perl-base 4·zlib1g 1·libxml2 1) | — | — | 미정 | [주의] 업스트림 대기 |

**평가**: HuggingFace 공식 이미지(`ghcr.io/huggingface/...`)를 그대로 사용 중 — 자체 빌드가 아니라
Dockerfile 수정으로 해소 불가. **최신 `cpu-1.6.x` 패치 태그로 재반입**하면 openssl/libgnutls/libxml2
3건은 해소될 가능성이 높다(업스트림 이미지가 베이스를 재빌드했다면).

### 3-D. `ollama/ollama:latest`

CRITICAL 0건. HIGH 33건은 `scans/ollama_ollama_latest.json` 참고(주로 Go 모듈 의존성).

---

## 4. 제한사항 (Limitations)

- **시점 스캔(point-in-time)**: 2026-08-04 기준 Trivy DB(2026-08-04 01:17 UTC 업데이트)로 스캔한
  결과다. 신규 CVE 는 매일 추가되므로, 실제 반입 직전 재스캔을 권고한다(§5 재현 방법 참고).
  `ollama/ollama:latest` 처럼 `latest` 태그를 쓰는 이미지는 반입 시점에 실제로 받은 이미지의
  digest(`docker inspect --format='{{.Id}}'`)를 별도 기록해 이 보고서의 Image ID(§1)와 일치하는지
  확인할 것 — 태그는 같아도 원격 저장소의 `latest` 가 갱신되면 다른 이미지일 수 있다.
- **정적 스캔 한정**: OS 패키지·언어 패키지(Python 등) 메타데이터 기반 정적 취약점 매칭이다.
  런타임 동작 스캔(DAST), 시크릿 스캔(자격증명 하드코딩 등), 코드 자체 SAST 는 범위 밖 —
  필요 시 `trivy fs`(코드 스캔)·`trivy image --scanners secret` 별도 실행 가능.
- **CRITICAL/HIGH 라도 실제 공격표면과 무관할 수 있음**: §3-A 의 `perl-base` 처럼 이미지에 설치는
  됐으나 애플리케이션이 호출하지 않는 패키지는 심각도 라벨과 별개로 실질 위험이 낮다 — 본 보고서는
  이런 맥락을 최대한 병기했으나, 최종 위험 수용 여부는 제출처 보안 심의 기준에 따른다.
- **`nice/migrate` vs `nice/migrate2`**: 완전히 동일한 이미지(§1 주석). 두 이름으로 반입해도 실제
  자산은 하나이므로 이중 계상하지 않도록 유의.

---

## 5. 재현 방법 (다른 사람이 동일 결과를 재현하려면)

```bash
# Trivy 설치 (버전 고정 권장)
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
  | sh -s -- -b /usr/local/bin v0.73.0

# 이미지별 SBOM(CycloneDX) + 취약점 스캔(JSON) 생성 — 예: nice/migrate:dev
trivy image --format cyclonedx --output nice_migrate.cdx.json nice/migrate:dev
trivy image --format json      --output nice_migrate.json     nice/migrate:dev

# 사람이 보기 좋은 표 형태로 즉석 확인
trivy image --severity CRITICAL,HIGH nice/migrate:dev
```

---

## 6. 첨부 파일 목록

```
docs/security/
├── SECURITY_VERIFICATION_20260804.md   (본 문서)
├── SECURITY_VERIFICATION_20260804.pdf  (PDF 버전, 제출용)
├── scan_summary.json                   (심각도 집계 + CRITICAL/HIGH 상세 — 이 문서의 원천 데이터)
├── sbom/
│   ├── nice_migrate.cdx.json           (= nice_migrate2.cdx.json, 동일 이미지)
│   ├── nice_migrate2.cdx.json
│   ├── nice_rag-server.cdx.json
│   ├── nice_ingestion.cdx.json
│   ├── nice_shock-server.cdx.json
│   ├── nice_postgres_pg16.cdx.json
│   ├── ollama_ollama_latest.cdx.json
│   └── tei_cpu-1.6.cdx.json
└── scans/                              (Trivy 원본 결과 — CVE 전체 목록, CVSS, 참고링크 포함)
    ├── nice_migrate.json (= nice_migrate2.json)
    ├── nice_migrate2.json
    ├── nice_rag-server.json
    ├── nice_ingestion.json
    ├── nice_shock-server.json
    ├── nice_postgres_pg16.json
    ├── ollama_ollama_latest.json
    └── tei_cpu-1.6.json
```
