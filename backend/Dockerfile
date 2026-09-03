# Razo_AI backend — FastAPI on uvicorn.
# Runs anywhere that takes a container: Render, Fly.io, Cloud Run, plain Docker.
FROM python:3.12-slim

# Fail fast and log straight through — a buffered crash log helps nobody.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code edit doesn't re-resolve the whole tree.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Never run as root in a published container.
RUN useradd --create-home --uid 10001 razo && chown -R razo:razo /app
USER razo

# Hosts inject their own $PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

# Shell form, because $PORT has to expand at start time, not build time.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
