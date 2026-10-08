# Portal de auditoria (Fase 9) — docs/audit.md
FROM python:3.13.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --no-create-home audit
COPY portal ./portal

USER 10001
EXPOSE 8080
CMD ["uvicorn", "portal.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header", "--log-level", "info"]
