#!/bin/sh
set -eu

{
    printf '%s\n' \
        'CREATE TABLE IF NOT EXISTS public.schema_migrations (' \
        '    version text PRIMARY KEY,' \
        '    applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP' \
        ');' \
        "SELECT pg_advisory_lock(hashtext('agenteia-schema-migrations'));"

    for migration in /migrations/*.sql; do
        version=$(basename "$migration")
        printf '%s\n' \
            "SELECT EXISTS (SELECT 1 FROM public.schema_migrations WHERE version = '$version') AS applied \\gset" \
            '\if :applied' \
            "\\echo migration already applied: $version" \
            '\else' \
            "\\echo applying migration: $version" \
            "\\i $migration" \
            "INSERT INTO public.schema_migrations (version) VALUES ('$version');" \
            '\endif'
    done

    printf '%s\n' \
        "SELECT pg_advisory_unlock(hashtext('agenteia-schema-migrations'));" \
        "\\echo database migrations completed"
} | psql -v ON_ERROR_STOP=1

