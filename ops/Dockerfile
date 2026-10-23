FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 REPORT_STORAGE_DIR=/app/data/reports
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/app /app/app
COPY backend/static /app/static
COPY backend/alembic.ini /app/alembic.ini
COPY backend/migrations /app/migrations
COPY backend/scripts/init_instance.py /app/scripts/init_instance.py
RUN useradd -r -u 10001 appuser && mkdir -p /app/data && chown -R appuser:appuser /app/data
USER appuser
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
