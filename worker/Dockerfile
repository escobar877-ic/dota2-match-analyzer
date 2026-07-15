FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY worker ./worker
COPY ml ./ml
COPY config ./config
COPY imports ./imports

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "assert b'sleep' in open('/proc/1/cmdline', 'rb').read()"

CMD ["sleep", "infinity"]
