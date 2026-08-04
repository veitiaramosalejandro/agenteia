@echo off
setlocal enabledelayedexpansion

echo ========================================
echo  🖥️ Iniciando Machining Assistant UI...
echo ========================================
echo.

REM Usar rutas relativas al script para que funcione desde cualquier worktree
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\"
set "SERVICE_DIR=%SCRIPT_DIR%"

REM CAMBIAR AL DIRECTORIO DE AGENT-SERVICE
cd /d "%SERVICE_DIR%"

REM Activar entorno virtual
if exist "%PROJECT_ROOT%venv_machining\Scripts\activate.bat" (
    call "%PROJECT_ROOT%venv_machining\Scripts\activate.bat"
) else (
    echo ❌ No se encontró el entorno virtual.
    pause
    exit /b 1
)

REM Configurar URL de la API
set API_URL=http://localhost:8000/api/v1/agent

REM Verificar que el backend esté corriendo
echo 🔍 Verificando backend...

curl -s http://localhost:8000/api/v1/agent/health > nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ El backend no está corriendo en localhost:8000
    echo    Ejecuta primero: run_local.bat
    pause
    exit /b 1
)
echo   ✅ Backend: OK

echo.
echo ========================================
echo  🖥️ Abriendo UI en http://localhost:8501
echo  ⏹️  Presiona Ctrl+C para detener
echo ========================================
echo.

REM CAMBIAR AL DIRECTORIO DE AGENT-SERVICE
cd /d "%SERVICE_DIR%"

REM Ejecutar Streamlit con app/ui.py
streamlit run app/ui.py --server.port 8501 --server.address localhost

pause