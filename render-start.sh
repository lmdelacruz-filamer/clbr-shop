#!/usr/bin/env bash
# Render start step (demo hosting only). Runs every time the free service wakes up.
set -o errexit
python manage.py migrate --noinput
python manage.py seed_products            # recreates the 5 demo products + photos (free disk is wiped on restart)
python manage.py create_demo_staff        # makes the demo staff login from env vars
exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-10000} --workers 2 --timeout 60 --access-logfile -
