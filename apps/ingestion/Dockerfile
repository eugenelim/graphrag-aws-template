# Fargate ingestion task image. Build context is the repo root:
#   docker build -f apps/ingestion/Dockerfile -t graphrag-ingestion .
FROM python:3.12-slim

WORKDIR /app

# Install the graphrag library (runtime deps: pyyaml + boto3).
COPY pyproject.toml ./
COPY packages/ ./packages/
RUN pip install --no-cache-dir .

COPY apps/ingestion/ ./apps/ingestion/

# Runs once per invocation, then exits — on-demand, scale-to-zero (ADR-0002).
ENTRYPOINT ["python", "apps/ingestion/entrypoint.py"]
