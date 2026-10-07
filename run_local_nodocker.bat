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
set "ENV_FILE=%PROJECT_ROOT%.env"
set "PYTHON_EXE="

echo ========================================
echo  🚀 Iniciando Machining Agent API (sin Docker)
echo ========================================
echo.

if defined VIRTUAL_ENV (
    if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
            set PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe
        echo 🐍 Usando entorno activo: %VIRTUAL_ENV%
    )
)

if not defined PYTHON_EXE if exist "%PROJECT_ROOT%venv_machining\Scripts\python.exe" (
    set PYTHON_EXE=%PROJECT_ROOT%venv_machining\Scripts\python.exe
    call "%PROJECT_ROOT%venv_machining\Scripts\activate.bat"
    echo 🐍 Usando entorno del proyecto: %PROJECT_ROOT%venv_machining
)

if not defined PYTHON_EXE (
    echo ❌ No se encontró un entorno virtual utilizable.
    echo    Activa un venv o ejecuta primero: setup.bat
    pause
    exit /b 1
)

cd /d "%SERVICE_DIR%"

REM Cargar variables desde .env del proyecto (clave=valor).
if exist "%ENV_FILE%" (
    echo 📄 Cargando variables desde %ENV_FILE%
    for /f "usebackq tokens=* delims=" %%L in ("%ENV_FILE%") do (
        set "line=%%L"
        if not "!line!"=="" if not "!line:~0,1!"=="#" (
            for /f "tokens=1* delims==" %%A in ("!line!") do (
                if not "%%A"=="" set "%%A=%%B"
            )
        )
    )
) else (
    echo ⚠️ No se encontró %ENV_FILE%. Se usarán variables ya definidas en el entorno.
)

REM Variables mínimas del proceso local.
if not defined ENVIRONMENT set "ENVIRONMENT=development"
set "PYTHONPATH=%PYTHONPATH%;%SERVICE_DIR%"

REM Fallbacks por si faltan claves en .env.
if not defined OLLAMA_BASE_URL set "OLLAMA_BASE_URL=http://localhost:11435"
if not defined VECTOR_DB_URL set "VECTOR_DB_URL=http://localhost:6333"
if not defined REDIS_URL set "REDIS_URL=redis://localhost:6379"

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

"%PYTHON_EXE%" -c "import uvicorn, click" > nul 2>&1
if %errorlevel% neq 0 (
    echo ⚙️ Dependencias runtime faltantes. Instalando uvicorn y click...
    "%PYTHON_EXE%" -m pip install uvicorn click > nul 2>&1
    "%PYTHON_EXE%" -c "import uvicorn, click" > nul 2>&1
    if %errorlevel% neq 0 (
        echo ❌ No se pudieron instalar uvicorn/click en el entorno activo.
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

pause