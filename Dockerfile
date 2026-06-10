FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY revops/ revops/

EXPOSE 8000

# The app seeds the warehouse automatically on first startup.
# $PORT is set by hosts like Render/Railway; defaults to 8000 locally.
CMD ["sh", "-c", "uvicorn revops.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
