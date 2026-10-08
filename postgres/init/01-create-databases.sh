#!/usr/bin/env bash
# Executado pela imagem oficial do PostgreSQL apenas na primeira inicialização
# (volume vazio). Cria um banco e um usuário dedicados por componente.
set -euo pipefail

create_db() {
  local db="$1" user="$2" password="$3"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v db="$db" -v usr="$user" -v pwd="$password" <<'SQL'
CREATE ROLE :"usr" LOGIN PASSWORD :'pwd';
CREATE DATABASE :"db" OWNER :"usr";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
SQL
}

create_db langfuse langfuse "$LANGFUSE_DB_PASSWORD"
create_db litellm  litellm  "$LITELLM_DB_PASSWORD"
