FROM python:3.12.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /site
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home site
COPY app.py retail_catalog.py cloud_catalog.py ./
COPY templates ./templates
COPY static/app.js static/style.css ./static/
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60", "--worker-tmp-dir", "/tmp", "--error-logfile", "-", "app:app"]
