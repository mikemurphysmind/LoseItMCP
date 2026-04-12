FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV LOSEIT_DB=/data/loseit.db \
    LOSEIT_HOST=0.0.0.0 \
    LOSEIT_PORT=8000 \
    LOSEIT_TRANSPORT=streamable-http

EXPOSE 8000

CMD ["python", "src/server.py"]
