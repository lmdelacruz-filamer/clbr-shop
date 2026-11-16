#!/bin/sh
set -e
if ! ls store/migrations/0001_*.py >/dev/null 2>&1; then
  echo "ERROR: store/migrations is missing. Run 'python manage.py makemigrations store' once locally and redeploy (see DEPLOY.md step 3)." >&2
  exit 1
fi
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec "$@"
