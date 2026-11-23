FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gcc \
    git \
    libffi-dev \
    libjpeg62-turbo-dev \
    libssl-dev \
    libtesseract-dev \
    libleptonica-dev \
    pkg-config \
    g++ \
    tesseract-ocr-rus \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements-docker.txt ./
COPY estd/requirements.txt estd/requirements.txt
RUN python -m pip install --upgrade pip && \
    pip install -r requirements-docker.txt

COPY . .

RUN ln -s "$(dirname "$(find /usr/share/tesseract-ocr -name rus.traineddata -print -quit)")" /opt/tessdata \
    && test -f /opt/tessdata/rus.traineddata
ENV TESSDATA_PREFIX=/opt/tessdata

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
