#!/usr/bin/env bash
# Render build step (demo hosting only).
set -o errexit
pip install --upgrade pip
pip install -r requirements.txt
# Download Tailwind / QR libs once so the site serves them itself (falls back to CDN if this fails).
DJANGO_DEBUG=1 USE_SQLITE=1 python manage.py vendor_assets || true
python manage.py collectstatic --noinput
