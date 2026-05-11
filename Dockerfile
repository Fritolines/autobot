FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies before copying source for better layer caching.
# Deps are listed explicitly so source-only changes don't re-trigger pip.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.30.0" \
    "ccxt>=4.0.0" \
    "pandas>=2.0.0" \
    "numpy>=1.24.0" \
    "aiohttp>=3.9.0" \
    "pydantic>=2.0.0" \
    "aiosqlite>=0.20.0" \
    "websockets>=12.0" \
    "python-dotenv>=1.0.0" \
    "pyarrow>=14.0.0"

COPY . .

EXPOSE 8000

CMD ["python", "-m", "src"]
