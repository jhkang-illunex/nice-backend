-- 아키텍처 설계서 §2.2

CREATE TABLE IF NOT EXISTS firms (
    firm_id                   VARCHAR(6) PRIMARY KEY,
    biz_no                    VARCHAR(13) NOT NULL,
    rep_bizno                 VARCHAR(13),
    firm_name                 TEXT NOT NULL,
    sector_code               VARCHAR(10),
    firm_data_type            VARCHAR(30),
    firm_confidence_level     VARCHAR(10),
    base_year                 INTEGER,
    sales_year_fin            NUMERIC(18,2),
    sales_year_vat_observed   NUMERIC(18,2),
    vat_fs_est_sales          NUMERIC(18,2),
    vat_fs_est_purchase       NUMERIC(18,2),
    inventory                 NUMERIC(18,2),
    value_added_year_fin      NUMERIC(18,2),
    employees_count           INTEGER,
    cri_score                 NUMERIC(4,2),
    cri_year                  INTEGER,
    watch_grade               VARCHAR(10),

    -- v2.2 시각화 캐시
    display_x                 REAL,
    display_y                 REAL,
    display_size              REAL,
    industry_short_name       VARCHAR(50),
    ranking_in_sector         INTEGER,

    -- 검색용
    firm_name_tsv             tsvector,
    firm_name_embedding       vector(384),

    created_at                TIMESTAMP DEFAULT now(),
    updated_at                TIMESTAMP DEFAULT now()
);

-- §2.2.1 인덱스
CREATE INDEX IF NOT EXISTS firms_bizno_idx        ON firms (biz_no);
CREATE INDEX IF NOT EXISTS firms_rep_bizno_idx    ON firms (rep_bizno);
CREATE INDEX IF NOT EXISTS firms_sector_idx       ON firms (sector_code);
CREATE INDEX IF NOT EXISTS firms_base_year_idx    ON firms (base_year);
CREATE INDEX IF NOT EXISTS firms_name_trgm_idx    ON firms USING GIN (firm_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS firms_name_tsv_idx     ON firms USING GIN (firm_name_tsv);
CREATE INDEX IF NOT EXISTS firms_name_emb_idx     ON firms USING hnsw (firm_name_embedding vector_cosine_ops);

ALTER TABLE firms
    ADD CONSTRAINT firms_sector_fk
    FOREIGN KEY (sector_code) REFERENCES sectors(code)
    DEFERRABLE INITIALLY DEFERRED;
