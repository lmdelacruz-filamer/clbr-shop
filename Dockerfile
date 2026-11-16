FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && mkdir -p /app/media /app/staticfiles \
 && chmod +x /app/docker-entrypoint.sh && chown -R app:app /app
USER app
# Download Tailwind / QR libs / fonts once so the site serves them itself (falls back to CDN if this fails).
RUN DJANGO_DEBUG=1 USE_SQLITE=1 python manage.py vendor_assets || true
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--access-logfile", "-"]
