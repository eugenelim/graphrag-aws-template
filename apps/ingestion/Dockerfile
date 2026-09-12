# Fargate ingestion task image. Build context is the repo root:
#   docker build -f apps/ingestion/Dockerfile -t graphrag-ingestion .
FROM python:3.12-slim

# Apply outstanding Debian security updates on every build. The base image lags the
# trixie security archive, so a rebuild alone does not clear OS CVEs — without this the
# image ships whatever openssl/perl/util-linux the base was baked with. Keeping it here
# (rather than pinning a fixed base digest) makes each rebuild self-healing.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the graphrag library + the ingest extra (runtime deps: pyyaml + boto3; ingest
# adds networkx for in-task Louvain community detection — ADR-0005, never the query Lambda).
COPY pyproject.toml ./
COPY packages/ ./packages/
RUN pip install --no-cache-dir ".[ingest]"

COPY apps/ingestion/ ./apps/ingestion/

# Runs once per invocation, then exits — on-demand, scale-to-zero (ADR-0002).
ENTRYPOINT ["python", "apps/ingestion/entrypoint.py"]
