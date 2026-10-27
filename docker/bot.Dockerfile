# Claude Code is a native binary; install it with npm in a throwaway stage and copy only the binary.
FROM node:24.11.0-bookworm-slim AS claude
RUN npm install -g @anthropic-ai/claude-code@2.1.311 \
 && cp "$(readlink -f "$(command -v claude)")" /claude \
 && /claude --version

FROM python:3.12.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/home DISABLE_AUTOUPDATER=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates ripgrep \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --home-dir /home/app app
COPY --from=claude /claude /usr/local/bin/claude
WORKDIR /app
COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt
COPY claude_trader ./claude_trader
COPY config/config.yaml ./config/config.yaml
USER 10001
CMD ["python", "-m", "claude_trader"]
