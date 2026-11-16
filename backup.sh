#!/bin/sh
# Nightly backup of database + product images. Cron example:  0 3 * * * cd /srv/clbr && ./backup.sh
set -e
mkdir -p backups
# Credentials come from the db container's own environment, so .env is never sourced by the shell.
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "backups/db-$(date +%F).sql.gz"
docker compose exec -T web tar -C /app -czf - media > "backups/media-$(date +%F).tar.gz"
find backups -type f -mtime +14 -delete      # keep 14 days; copy backups/ off the server too!
