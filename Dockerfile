# Live demo image (Hugging Face Spaces, Docker SDK): demo UI + memory-mcp in one process on $PORT.
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY memory_mcp memory_mcp
COPY agent agent
COPY demo demo
COPY scripts scripts

# Spaces run the container as uid 1000; keep the sandbox database somewhere writable.
ENV SERVE_MCP=1 PORT=7860 MEMORY_DB=/tmp/memory.db APPROVAL_TIMEOUT=120 PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["sh", "-c", "python -m scripts.seed && python -m demo.app"]
