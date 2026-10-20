#!/bin/sh
set -eu

until pg_isready -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}"; do
    sleep 3
done

psql -v ON_ERROR_STOP=1 -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP);"

for migration in /migrations/*.sql; do
    version=$(basename "$migration")
    applied=$(psql -Atq -c "SELECT 1 FROM public.schema_migrations WHERE version = '$version' LIMIT 1;")
    if [ "$applied" = "1" ]; then
        echo "migration already applied: $version"
        continue
    fi
    echo "applying migration: $version"
    psql -v ON_ERROR_STOP=1 -f "$migration"
    psql -v ON_ERROR_STOP=1 -c "INSERT INTO public.schema_migrations (version) VALUES ('$version');"
done

echo "database migrations completed"
