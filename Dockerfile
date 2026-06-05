# Bet Decoder — self-hosted single-container image.
# Build:  docker build -t bet-decoder .
# Run:    docker run -p 8000:8000 --env-file .env bet-decoder
FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching. numpy/scipy ship manylinux
# wheels, so no system build toolchain is needed on python:3.11-slim.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source. .dockerignore keeps secrets, the local DB, caches and the
# .claude worktrees out of the image.
COPY . .

# SQLite db (pricelens.db) is created at runtime by db.init_db(); persist it
# with a volume if you want it to survive container restarts:
#   docker run -p 8000:8000 -v "$PWD/data:/app" --env-file .env bet-decoder
EXPOSE 8000

# Shell form so $PORT (Render / Cloud hosts) is honored; defaults to 8000,
# which matches Hugging Face Spaces app_port: 8000.
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
