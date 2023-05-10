# syntax=docker/dockerfile:1

# ---- build stage: resolve and install dependencies into a venv -------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


# ---- runtime stage: no build tooling, no compilers, no pip cache -----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # matplotlib writes a font cache at import time and must not try to use a
    # read-only or non-existent home directory.
    MPLCONFIGDIR=/tmp/matplotlib

RUN useradd --create-home --uid 10001 app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . /app

USER app

# Collected at build time so the runtime container needs no writable app dir.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && python -c "import matplotlib.pyplot"  # warm the font cache

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# Two workers x four threads: the chart render is CPU-bound but short, and the
# cache absorbs the repeat traffic.
CMD ["gunicorn", "graphql.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-"]
