FROM python:3.13-slim

# Cloud Run sets PORT (8080); HOSTED=1 turns on the public-site behaviour in server.py.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOSTED=1 PORT=8080
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the server needs: the page, the server, and the extension (offered as a download).
COPY server.py index.html ./
COPY extension/ extension/

RUN useradd --create-home app
USER app

EXPOSE 8080
CMD ["python", "server.py"]
