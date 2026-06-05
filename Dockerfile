FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user; clips/ must be writable by it.
RUN useradd --create-home --uid 10001 highlightz \
    && mkdir -p /app/clips \
    && chown -R highlightz:highlightz /app/clips
USER highlightz

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Inside the container, listen on all interfaces (the container boundary is the
# isolation); publish only behind a reverse proxy in production.
ENV DASHBOARD_HOST=0.0.0.0

EXPOSE 8000

CMD ["python", "-m", "src.main"]
