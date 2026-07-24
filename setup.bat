@echo off
setlocal enabledelayedexpansion

echo ========================================
echo  ⚙️ Configurando entorno de desarrollo
echo ========================================
echo.

REM 1. Levantar servicios Docker
echo [1/5] 🐳 Levantando servicios Docker...
docker-compose -f docker-compose.dev.yml up -d

echo.
echo ⏳ Esperando que los servicios estén listos...
timeout /t 15 /nobreak > nul

REM 2. Descargar modelos
echo.
echo [2/5] 📦 Descargando modelos de Ollama...
docker exec machining_ollama ollama pull qwen2.5:7b
docker exec machining_ollama ollama pull nomic-embed-text

REM 3. Crear entorno virtual
echo.
echo [3/5] 🐍 Creando entorno virtual...
if not exist "venv_machining" (
    python -m venv venv_machining
    echo    ✅ Entorno virtual creado
) else (
    echo    ℹ️ El entorno virtual ya existe
)

REM 4. Activar e instalar dependencias
echo.
echo [4/5] 📦 Instalando dependencias...
call venv_machining\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

REM 5. Verificar servicios
echo.
echo [5/5] ✅ Verificando servicios...

REM Verificar Ollama
curl -s http://localhost:11435/api/tags > nul 2>&1
if %errorlevel% == 0 (
    echo   ✅ Ollama: OK
) else (
    echo   ❌ Ollama: ERROR - Asegurate de que Docker este corriendo
)

REM Verificar Qdrant
curl -s http://localhost:6333/collections > nul 2>&1
if %errorlevel% == 0 (
    echo   ✅ Qdrant: OK
) else (
    echo   ❌ Qdrant: ERROR
)

REM Verificar Redis
ping -n 1 localhost > nul
redis-cli -h localhost -p 6379 ping > nul 2>&1
if %errorlevel% == 0 (
    echo   ✅ Redis: OK
) else (
    echo   ❌ Redis: ERROR
)

echo.
echo ========================================
echo  🎉 ¡Entorno listo!
echo ========================================
echo.
echo Para iniciar el proyecto:
echo   1. Backend: run_local.bat
echo   2. UI:      run_ui.bat (en otra terminal)
echo.
echo O en una sola terminal:
echo   - En background: start /B run_local.bat ^> backend.log 2^>^&1
echo   - Luego: run_ui.bat
echo.

pause