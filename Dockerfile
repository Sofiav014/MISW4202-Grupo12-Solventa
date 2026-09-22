FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY gateway ./gateway
COPY dobles_http ./dobles_http
COPY scripts ./scripts
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "gateway.servidor"]
