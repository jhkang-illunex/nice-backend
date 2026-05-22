-- 아키텍처 설계서 §2.7
-- 마스터 테이블. firms 의 FK 가 sectors 를 참조할 수 있으므로 firms 보다 먼저 적재.

CREATE TABLE IF NOT EXISTS sectors (
    code          VARCHAR(10) PRIMARY KEY,
    name          TEXT,
    level         INTEGER,
    parent_code   VARCHAR(10),
    color         VARCHAR(7)
);

CREATE TABLE IF NOT EXISTS hs_codes (
    code         VARCHAR(6) PRIMARY KEY,
    name         TEXT,
    hs2          VARCHAR(2),
    hs4          VARCHAR(4),
    elasticity   NUMERIC(6,2)
);

CREATE TABLE IF NOT EXISTS countries (
    iso_alpha2   VARCHAR(2) PRIMARY KEY,
    name_kr      TEXT,
    name_en      TEXT
);
