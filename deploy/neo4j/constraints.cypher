// Neo4j 설계서 §7.3 — 모든 PK 는 UNIQUE 제약 (MERGE 성능 + 무결성)
// 노드 9종

CREATE CONSTRAINT firm_pk           IF NOT EXISTS FOR (n:Firm)          REQUIRE n.firm_id          IS UNIQUE;
CREATE CONSTRAINT headquarter_pk    IF NOT EXISTS FOR (n:Headquarter)   REQUIRE n.rep_bizno        IS UNIQUE;
CREATE CONSTRAINT sector_pk         IF NOT EXISTS FOR (n:Sector)        REQUIRE n.code             IS UNIQUE;
CREATE CONSTRAINT hscode_pk         IF NOT EXISTS FOR (n:HSCode)        REQUIRE n.code             IS UNIQUE;
CREATE CONSTRAINT country_pk        IF NOT EXISTS FOR (n:Country)       REQUIRE n.iso_alpha2       IS UNIQUE;
CREATE CONSTRAINT year_pk           IF NOT EXISTS FOR (n:Year)          REQUIRE n.year             IS UNIQUE;
CREATE CONSTRAINT scenario_pk       IF NOT EXISTS FOR (n:Scenario)      REQUIRE n.scenario_id      IS UNIQUE;
CREATE CONSTRAINT shock_pk          IF NOT EXISTS FOR (n:Shock)         REQUIRE n.shock_id         IS UNIQUE;
CREATE CONSTRAINT simrun_pk         IF NOT EXISTS FOR (n:SimulationRun) REQUIRE n.run_id           IS UNIQUE;
