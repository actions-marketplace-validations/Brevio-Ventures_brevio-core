FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY brevio ./brevio
RUN pip install --no-cache-dir ".[mcp]"
USER nobody
ENTRYPOINT ["brevio-core", "mcp"]
