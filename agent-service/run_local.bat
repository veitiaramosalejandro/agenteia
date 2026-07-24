@echo off
setlocal enabledelayedexpansion

echo ========================================
echo  🚀 Iniciando Machining Agent API...
echo ========================================
echo.

REM Activar entorno virtual
if exist "venv_machining\Scripts\activate.bat" (
    call venv_machining\Scripts\activate.bat
) else (
    echo ❌ No se encontró el entorno virtual.
    echo    Ejecuta primero: setup.bat
    pause
    exit /b 1
)

REM Verificar que los servicios Docker estén corriendo
echo 🔍 Verificando servicios...

curl -s http://localhost:11435/api/tags > nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Ollama no está corriendo.
    echo    Ejecuta: docker-compose -f docker-compose.dev.yml up -d
    pause
    exit /b 1
)
echo   ✅ Ollama: OK

curl -s http://localhost:6333/collections > nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Qdrant no está corriendo.
    echo    Ejecuta: docker-compose -f docker-compose.dev.yml up -d
    pause
    exit /b 1
)
echo   ✅ Qdrant: OK

REM Configurar variables de entorno
set PYTHONPATH=%PYTHONPATH%;%CD%
set ENVIRONMENT=development

REM Descargar modelos de Ollama si no existen
echo.
echo 📦 Verificando modelos de Ollama...

curl -s http://localhost:11435/api/tags | findstr "qwen2.5" > nul
if %errorlevel% neq 0 (
    echo ⬇️ Descargando modelo qwen2.5...
    docker exec machining_ollama ollama pull qwen2.5:7b
) else (
    echo   ✅ qwen2.5: OK
)

curl -s http://localhost:11435/api/tags | findstr "nomic-embed-text" > nul
if %errorlevel% neq 0 (
    echo ⬇️ Descargando modelo nomic-embed-text...
    docker exec machining_ollama ollama pull nomic-embed-text
) else (
    echo   ✅ nomic-embed-text: OK
)

REM Inicializar la base de datos vectorial (primera vez)
echo.
echo 🗄️ Inicializando Qdrant...
python -c "from qdrant_client import QdrantClient; QdrantClient(url='http://localhost:6333').get_collections()"

REM Ejecutar ingesta inicial si es necesario
if not exist ".ingest_done" (
    echo.
    echo 📥 Ejecutando ingesta inicial...
    python -m app.system.ingest
    echo. > .ingest_done
    echo   ✅ Ingesta completada
)

echo.
echo ========================================
echo  🌐 Iniciando servidor en http://localhost:8000
echo  📚 Documentación: http://localhost:8000/docs
echo  ⏹️  Presiona Ctrl+C para detener
echo ========================================
echo.

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

pause