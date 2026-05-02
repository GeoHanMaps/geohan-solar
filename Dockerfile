FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin libgdal-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

ENV GDAL_CONFIG=/usr/bin/gdal-config

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd -r geohan && useradd -r -g geohan -d /home/geohan geohan \
    && mkdir -p /home/geohan/.config/earthengine /app/data/maps \
    && chown -R geohan:geohan /app /home/geohan

COPY --chown=geohan:geohan . .

USER geohan

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
