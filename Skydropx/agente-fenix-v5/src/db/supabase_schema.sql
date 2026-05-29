-- ===========================================================================
-- Schema Supabase para Agente Fénix v5.3
-- Idéntico a SQLite local pero adaptado a Postgres/Supabase
-- ===========================================================================

-- Habilitar extensión para fuzzy matching (opcional)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- TABLA: companies (entidades empresariales únicas)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fenix_companies (
    id                      BIGSERIAL PRIMARY KEY,
    fingerprint             TEXT UNIQUE NOT NULL,
    razon_social            TEXT,
    nombre_comercial        TEXT,
    rfc                     TEXT,
    estado                  TEXT,
    municipio               TEXT,
    colonia                 TEXT,
    cp                      TEXT,
    direccion               TEXT,
    scian                   TEXT,
    giro_descripcion        TEXT,
    tamano                  TEXT,
    modelo_negocio          TEXT,
    skydropx_plan           TEXT,
    longitud                DOUBLE PRECISION,
    latitud                 DOUBLE PRECISION,
    score_data              INTEGER DEFAULT 0,
    score_skydropx          INTEGER DEFAULT 0,
    score_sales             INTEGER DEFAULT 0,
    score_contact           INTEGER DEFAULT 0,
    bucket                  TEXT DEFAULT 'RAW',
    tipo_lead               TEXT DEFAULT 'frio',
    first_seen_at           TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at            TIMESTAMPTZ DEFAULT NOW(),
    times_seen              INTEGER DEFAULT 1,
    -- Sincronización con SQLite local
    local_id                BIGINT,
    synced_at               TIMESTAMPTZ DEFAULT NOW(),
    source_system           TEXT DEFAULT 'fenix_local',
    metadata                JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_fenix_companies_estado ON fenix_companies(estado);
CREATE INDEX IF NOT EXISTS idx_fenix_companies_scian ON fenix_companies(scian);
CREATE INDEX IF NOT EXISTS idx_fenix_companies_bucket ON fenix_companies(bucket);
CREATE INDEX IF NOT EXISTS idx_fenix_companies_score ON fenix_companies(score_data DESC);
CREATE INDEX IF NOT EXISTS idx_fenix_companies_local_id ON fenix_companies(local_id);

-- ============================================================================
-- TABLA: contacts (emails, teléfonos, redes sociales)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fenix_contacts (
    id                      BIGSERIAL PRIMARY KEY,
    company_id              BIGINT NOT NULL REFERENCES fenix_companies(id) ON DELETE CASCADE,
    kind                    TEXT NOT NULL,
    value                   TEXT NOT NULL,
    value_norm              TEXT NOT NULL,
    is_primary              INTEGER DEFAULT 0,
    is_personal             INTEGER DEFAULT 0,
    is_verified             INTEGER DEFAULT 0,
    verification_status     TEXT,
    verified_at             TIMESTAMPTZ,
    fuente                  TEXT,
    found_at                TIMESTAMPTZ DEFAULT NOW(),
    synced_at               TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, kind, value_norm)
);

CREATE INDEX IF NOT EXISTS idx_fenix_contacts_value ON fenix_contacts(value_norm);
CREATE INDEX IF NOT EXISTS idx_fenix_contacts_kind ON fenix_contacts(kind);
CREATE INDEX IF NOT EXISTS idx_fenix_contacts_company ON fenix_contacts(company_id);

-- ============================================================================
-- TABLA: jobs (historial de corridas del pipeline)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fenix_jobs (
    job_id                  TEXT PRIMARY KEY,
    nicho                   TEXT,
    zona                    TEXT,
    modelo                  TEXT,
    meta                    INTEGER,
    estrategia              TEXT,
    started_at              TIMESTAMPTZ DEFAULT NOW(),
    finished_at             TIMESTAMPTZ,
    n_candidatos            INTEGER DEFAULT 0,
    n_new                   INTEGER DEFAULT 0,
    n_updated               INTEGER DEFAULT 0,
    n_duplicates            INTEGER DEFAULT 0,
    n_completo              INTEGER DEFAULT 0,
    duration_sec            REAL,
    synced_at               TIMESTAMPTZ DEFAULT NOW(),
    source_system           TEXT DEFAULT 'fenix_local',
    errors                  JSONB DEFAULT '[]'::jsonb,
    stats                   JSONB DEFAULT '{}'::jsonb,
    exports                 JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_fenix_jobs_started ON fenix_jobs(started_at DESC);

-- ============================================================================
-- TABLA: sync_log (audit del sync local↔supabase)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fenix_sync_log (
    id                      BIGSERIAL PRIMARY KEY,
    sync_at                 TIMESTAMPTZ DEFAULT NOW(),
    direction               TEXT NOT NULL,        -- 'push' | 'pull'
    table_name              TEXT NOT NULL,
    n_inserted              INTEGER DEFAULT 0,
    n_updated               INTEGER DEFAULT 0,
    n_skipped               INTEGER DEFAULT 0,
    n_errors                INTEGER DEFAULT 0,
    duration_sec            REAL,
    error_sample            TEXT,
    source_system           TEXT
);

CREATE INDEX IF NOT EXISTS idx_fenix_sync_at ON fenix_sync_log(sync_at DESC);

-- ============================================================================
-- TABLA: opt_outs (compliance LFPDPPP)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fenix_opt_outs (
    id                      BIGSERIAL PRIMARY KEY,
    kind                    TEXT NOT NULL,
    value_norm              TEXT NOT NULL,
    reason                  TEXT,
    requested_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(kind, value_norm)
);

-- ============================================================================
-- VIEWS útiles para el equipo (vía Supabase Studio)
-- ============================================================================

-- Vista: leads PREMIUM listos para ventas
CREATE OR REPLACE VIEW fenix_leads_premium AS
SELECT
    c.id, c.razon_social, c.nombre_comercial, c.estado, c.municipio,
    c.giro_descripcion, c.tamano, c.modelo_negocio, c.skydropx_plan,
    c.score_data, c.bucket, c.tipo_lead, c.first_seen_at,
    (SELECT value FROM fenix_contacts WHERE company_id = c.id AND kind = 'email' LIMIT 1) AS email,
    (SELECT value FROM fenix_contacts WHERE company_id = c.id AND kind = 'phone' LIMIT 1) AS telefono,
    (SELECT value FROM fenix_contacts WHERE company_id = c.id AND kind = 'whatsapp' LIMIT 1) AS whatsapp,
    (SELECT value FROM fenix_contacts WHERE company_id = c.id AND kind = 'website' LIMIT 1) AS sitio_web
FROM fenix_companies c
WHERE c.bucket = 'COMPLETO'
  AND EXISTS(SELECT 1 FROM fenix_contacts WHERE company_id = c.id AND kind = 'email')
  AND EXISTS(SELECT 1 FROM fenix_contacts WHERE company_id = c.id AND kind IN ('phone','whatsapp'));

-- Vista: stats por job
CREATE OR REPLACE VIEW fenix_jobs_summary AS
SELECT
    job_id, nicho, zona, modelo, started_at, finished_at,
    n_new, n_completo, duration_sec,
    CASE WHEN finished_at IS NOT NULL
         THEN EXTRACT(EPOCH FROM (finished_at - started_at))
         ELSE NULL END AS actual_duration_sec
FROM fenix_jobs
ORDER BY started_at DESC;

-- ============================================================================
-- Habilitar RLS (Row Level Security) — recomendado
-- ============================================================================
-- NOTA: cuando uses anon key en lugar de service_role, descomentar:
-- ALTER TABLE fenix_companies ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE fenix_contacts ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "Allow read for authenticated" ON fenix_companies FOR SELECT USING (auth.role() = 'authenticated');
