FROM python:3.12.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home --home-dir /home/app app
WORKDIR /app
COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt
COPY dashboard ./dashboard
USER 10001
EXPOSE 8080
CMD ["uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
