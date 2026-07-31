@echo off
setlocal enabledelayedexpansion

REM ------------------------------------------------------------
REM  Arranque local sin Docker
REM  Requiere que estes servicios existan fuera de Docker:
REM  - Ollama en http://172.16.10.160:11435
REM  - Qdrant en http://172.16.10.160:6333
REM  - Redis en redis://172.16.10.160:6379
REM  - SQL Server / PostgreSQL accesibles desde la red local
REM ------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%"
set "SERVICE_DIR=%PROJECT_ROOT%agent-service"

echo ========================================
echo  🚀 Iniciando Machining Agent API (sin Docker)
echo ========================================
echo.

if exist "%PROJECT_ROOT%venv_machining\Scripts\activate.bat" (
    call "%PROJECT_ROOT%venv_machining\Scripts\activate.bat"
) else (
    echo ❌ No se encontró el entorno virtual.
    echo    Ejecuta primero: setup.bat
    pause
    exit /b 1
)

cd /d "%SERVICE_DIR%"

REM Variables de entorno para ejecución local.
set "ENVIRONMENT=development"
set "PYTHONPATH=%PYTHONPATH%;%SERVICE_DIR%"
set "OLLAMA_BASE_URL=http://localhost:11435"
set "VECTOR_DB_URL=http://localhost:6333"
set "REDIS_URL=redis://localhost:6379"
set "MODEL_NAME=qwen2.5:7b"
set "EMBEDDING_MODEL_NAME=nomic-embed-text"
set "NOTIF_API_BACKGROUND_ENABLED=false"
set "DB_URL=postgresql://user:pass@localhost:5432/machining_db"

if not defined DB_URL (
    echo ⚠️ DB_URL no está definida. Si usas PostgreSQL/Timescale local, define DB_URL antes de iniciar.
)

echo.
echo 🔍 Verificando servicios locales...

set "OLLAMA_TAGS_URL=%OLLAMA_BASE_URL%/api/tags"
curl.exe -s "%OLLAMA_TAGS_URL%" > nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️ Ollama no respondió en %OLLAMA_TAGS_URL%.
    echo    Inicia Ollama manualmente antes de continuar.
) else (
    echo   ✅ Ollama: OK
)

curl.exe -s http://localhost:6333/collections > nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️ Qdrant no respondió en localhost:6333.
    echo    Inicia Qdrant manualmente antes de continuar.
) else (
    echo   ✅ Qdrant: OK
)

curl.exe -s http://localhost:6379 > nul 2>&1
if %errorlevel% neq 0 (
    echo   ℹ️ Redis: no verificado por HTTP. Si no usas Docker, confirma que el puerto 6379 esté abierto.
) else (
    echo   ✅ Redis: respuesta detectada
)

echo.
echo ========================================
echo  🌐 Iniciando servidor en http://0.0.0.0:8000
echo  📚 Documentación: http://0.0.0.0:8000/docs
echo  ⏹️  Presiona Ctrl+C para detener
echo ========================================
echo.

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

pause