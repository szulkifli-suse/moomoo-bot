FROM python:3.12.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/home
RUN useradd --uid 10001 --create-home --home-dir /home/app app
WORKDIR /app
COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt
COPY claude_trader ./claude_trader
COPY config/config.yaml ./config/config.yaml
USER 10001
CMD ["python", "-m", "claude_trader"]
