FROM python:3.12-slim

# Prevent .pyc files and force stdout/stderr to be unbuffered (so logs
# show up immediately in the platform's log stream).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DJANGO_SECRET_KEY is not required at build time (only at runtime), but
# collectstatic imports settings.py, which raises if it's missing and
# DEBUG is off. Provide a build-only placeholder; the real value comes
# from the platform's environment variables at container start.
RUN DJANGO_SECRET_KEY=build-time-placeholder-not-used-at-runtime \
    DJANGO_DEBUG=True \
    python manage.py collectstatic --noinput

EXPOSE 8080

# Migrations run as Fly's release_command (see fly.toml), not here --
# so they run exactly once per deploy, before traffic shifts to the
# new instance, regardless of how many workers start.
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 3"]
