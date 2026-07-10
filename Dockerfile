FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Containers must accept connections from the port mapping; the bare-metal
# default is 127.0.0.1 (nginx proxies via localhost).
ENV DASHBOARD_HOST=0.0.0.0

EXPOSE 8000
CMD ["python", "-m", "src.main"]
