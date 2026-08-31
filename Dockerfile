FROM python:3.13-slim

# Sem buffer: os prints da aplicacao precisam chegar ao log do Coolify na
# hora, e nao quando o buffer encher.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 finbox && chown -R finbox /app
USER finbox

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

# Um worker de proposito: as BackgroundTasks do FastAPI vivem no processo,
# e a varredura de mensagens orfas na subida assume um processo por vez.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
