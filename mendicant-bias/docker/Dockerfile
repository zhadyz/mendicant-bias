FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Copy and install packages
COPY packages/core/ /app/packages/core/
COPY packages/mcp-server/ /app/packages/mcp-server/
COPY packages/runtime/ /app/packages/runtime/
COPY packages/gateway/ /app/packages/gateway/
COPY packages/mendicant-bias/ /app/packages/mendicant-bias/

RUN pip install --no-cache-dir \
    /app/packages/core[all] \
    /app/packages/mcp-server \
    /app/packages/runtime \
    /app/packages/gateway \
    /app/packages/mendicant-bias

# Copy config
COPY packages/mendicant-bias/config/mendicant.yaml /app/mendicant.yaml

EXPOSE 8001

CMD ["mendicant", "serve"]
