# Minimal image for the generated URL-shortener service (standard library only).
#   docker build -t url-shortener demo
#   docker run --rm -p 8000:8000 url-shortener
#   docker run --rm -p 8000:8000 -e SHORTENER_STORE=sqlite -v $PWD/data:/data -e SHORTENER_DB_PATH=/data/shortener.db url-shortener
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SHORTENER_HOST=0.0.0.0 SHORTENER_PORT=8000 \
    SHORTENER_BASE_URL=http://localhost:8000

WORKDIR /app
COPY url_shortener/ ./url_shortener/
COPY openapi.yaml .

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=2s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=1).status==200 else 1)"

CMD ["python", "-m", "url_shortener.server"]
