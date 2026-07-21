FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema requeridas para librosa, scipy y audio
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsndfile1 \
    libsndfile1-dev \
    llvm \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Actualizar pip, setuptools y wheel para evitar fallos de compilación de wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

COPY ./app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]