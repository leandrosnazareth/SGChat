#!/usr/bin/env bash
# Trilha de acesso dos auditores (Fase 9) — instalação idempotente.
# Executado pelo one-shot audit-db-init (imagem postgres) como superusuário, a cada subida:
# cria o papel audit_portal, o banco audit (de propriedade de postgres, não do portal),
# a tabela audit.access_log, os triggers que impedem UPDATE/DELETE/TRUNCATE e as funções
# append_access_event (única forma de gravar; encadeia hashes SHA-256) e verify_access_chain.
# Guia: docs/audit.md
set -euo pipefail
: "${PGHOST:?}" "${PGPASSWORD:?}" "${AUDIT_DB_PASSWORD:?defina AUDIT_DB_PASSWORD no .env}"
export PGUSER=postgres

psql -v ON_ERROR_STOP=1 -d postgres -v pwd="$AUDIT_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE audit_portal LOGIN PASSWORD %L', :'pwd')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_portal') \gexec
ALTER ROLE audit_portal LOGIN PASSWORD :'pwd' NOSUPERUSER NOCREATEDB NOCREATEROLE;
SELECT 'CREATE DATABASE audit OWNER postgres'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'audit') \gexec
REVOKE ALL ON DATABASE audit FROM PUBLIC;
GRANT CONNECT ON DATABASE audit TO audit_portal;
SQL

psql -v ON_ERROR_STOP=1 -d audit <<'SQL'
CREATE SCHEMA IF NOT EXISTS audit AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE TABLE IF NOT EXISTS audit.access_log (
    id             bigint      PRIMARY KEY,
    occurred_at    timestamptz NOT NULL,
    event_type     text        NOT NULL DEFAULT 'AUDIT_ACCESS',
    action         text        NOT NULL CHECK (action IN ('LOGIN', 'LOGOUT', 'SEARCH_CONVERSATIONS', 'VIEW_CONVERSATION',
                                                          'EXPORT_CONVERSATION', 'VIEW_ACCESS_LOG')),
    outcome        text        NOT NULL CHECK (outcome IN ('ALLOWED', 'DENIED', 'FAILED')),
    actor          text        NOT NULL,
    actor_roles    text[]      NOT NULL DEFAULT '{}',
    channel        text        NOT NULL CHECK (channel IN ('portal', 'cli')),
    target_users   text[]      NOT NULL DEFAULT '{}',
    conversation_id text,
    trace_id       text,
    query          jsonb       NOT NULL DEFAULT '{}',
    reason         text,
    result_count   integer,
    client_ip      text,
    prev_hash      text        NOT NULL,
    hash           text        NOT NULL UNIQUE
);
CREATE SEQUENCE IF NOT EXISTS audit.access_log_id_seq OWNED BY audit.access_log.id;
CREATE INDEX IF NOT EXISTS access_log_actor_idx ON audit.access_log (actor, occurred_at);
CREATE INDEX IF NOT EXISTS access_log_conversation_idx ON audit.access_log (conversation_id);

-- Imutabilidade: nem o dono da tabela altera ou apaga eventos sem desligar os triggers.
CREATE OR REPLACE FUNCTION audit.reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit.access_log é somente inclusão (%)', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;
DROP TRIGGER IF EXISTS access_log_no_update ON audit.access_log;
CREATE TRIGGER access_log_no_update BEFORE UPDATE OR DELETE ON audit.access_log
    FOR EACH ROW EXECUTE FUNCTION audit.reject_change();
DROP TRIGGER IF EXISTS access_log_no_truncate ON audit.access_log;
CREATE TRIGGER access_log_no_truncate BEFORE TRUNCATE ON audit.access_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_change();

-- Representação canônica de um evento para o hash (mesma na gravação e na verificação).
CREATE OR REPLACE FUNCTION audit.event_digest(
    p_prev text, p_id bigint, p_at timestamptz, p_type text, p_action text, p_outcome text, p_actor text,
    p_roles text[], p_channel text, p_targets text[], p_conversation text, p_trace text, p_query jsonb,
    p_reason text, p_count integer, p_ip text
) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws(E'\x1f',
        p_prev, p_id::text, to_char(p_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), p_type, p_action,
        p_outcome, p_actor, array_to_string(p_roles, ','), p_channel, array_to_string(p_targets, ','),
        coalesce(p_conversation, ''), coalesce(p_trace, ''), p_query::text, coalesce(p_reason, ''),
        coalesce(p_count::text, ''), coalesce(p_ip, '')), 'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION audit.append_access_event(
    p_action text, p_outcome text, p_actor text, p_roles text[], p_channel text,
    p_targets text[] DEFAULT '{}', p_conversation text DEFAULT NULL, p_trace text DEFAULT NULL,
    p_query jsonb DEFAULT '{}', p_reason text DEFAULT NULL, p_count integer DEFAULT NULL, p_ip text DEFAULT NULL
) RETURNS TABLE (event_id bigint, event_hash text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
DECLARE
    v_prev text;
    v_id   bigint;
    v_at   timestamptz := clock_timestamp();
    v_hash text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('audit.access_log'));
    SELECT l.hash INTO v_prev FROM audit.access_log l ORDER BY l.id DESC LIMIT 1;
    v_prev := coalesce(v_prev, repeat('0', 64));
    v_id := nextval('audit.access_log_id_seq');
    v_hash := audit.event_digest(v_prev, v_id, v_at, 'AUDIT_ACCESS', p_action, p_outcome, p_actor,
                                 coalesce(p_roles, '{}'), p_channel, coalesce(p_targets, '{}'), p_conversation,
                                 p_trace, coalesce(p_query, '{}'), p_reason, p_count, p_ip);
    INSERT INTO audit.access_log (id, occurred_at, event_type, action, outcome, actor, actor_roles, channel, target_users,
                                  conversation_id, trace_id, query, reason, result_count, client_ip, prev_hash, hash)
    VALUES (v_id, v_at, 'AUDIT_ACCESS', p_action, p_outcome, p_actor, coalesce(p_roles, '{}'), p_channel,
            coalesce(p_targets, '{}'), p_conversation, p_trace, coalesce(p_query, '{}'), p_reason, p_count, p_ip,
            v_prev, v_hash);
    RETURN QUERY SELECT v_id, v_hash;
END $$;

-- Recalcula a cadeia inteira: total de eventos, primeiro id inválido (NULL = íntegra) e hash da cabeça.
CREATE OR REPLACE FUNCTION audit.verify_access_chain()
RETURNS TABLE (total bigint, first_invalid_id bigint, head_hash text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
DECLARE
    r audit.access_log%ROWTYPE;
    v_prev text := repeat('0', 64);
BEGIN
    total := 0; first_invalid_id := NULL; head_hash := NULL;
    FOR r IN SELECT * FROM audit.access_log ORDER BY id LOOP
        total := total + 1;
        IF first_invalid_id IS NULL AND (r.prev_hash <> v_prev OR r.hash <> audit.event_digest(
               r.prev_hash, r.id, r.occurred_at, r.event_type, r.action, r.outcome, r.actor, r.actor_roles, r.channel,
               r.target_users, r.conversation_id, r.trace_id, r.query, r.reason, r.result_count, r.client_ip)) THEN
            first_invalid_id := r.id;
        END IF;
        v_prev := r.hash;
        head_hash := r.hash;
    END LOOP;
    RETURN NEXT;
END $$;

-- O portal só grava pela função e só lê; não altera nada.
REVOKE ALL ON ALL TABLES IN SCHEMA audit FROM PUBLIC, audit_portal;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA audit FROM PUBLIC, audit_portal;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA audit FROM PUBLIC;
GRANT USAGE ON SCHEMA audit TO audit_portal;
GRANT SELECT ON audit.access_log TO audit_portal;
GRANT EXECUTE ON FUNCTION audit.append_access_event(text, text, text, text[], text, text[], text, text, jsonb, text, integer, text),
                          audit.verify_access_chain() TO audit_portal;
SQL

echo "audit-db-init: trilha de acesso pronta ($(psql -tA -d audit -c 'SELECT count(*) FROM audit.access_log') eventos)."
