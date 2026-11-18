@echo off
REM Enterprise Fabric AI - FALLBACK launcher for the old Streamlit UI.
REM The main launcher (run_demo.bat) uses the React UI. This one starts the API and Streamlit
REM in two windows; the browser opens at http://localhost:8501. Close both windows to stop.

cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found in %CD%\.venv
    pause
    exit /b 1
)
set HF_HUB_OFFLINE=1
set HF_HUB_VERBOSITY=error
set HF_HUB_DISABLE_PROGRESS_BARS=1
set TRANSFORMERS_VERBOSITY=error

.venv\Scripts\python.exe -m scripts.check_index_free
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

echo Starting the API (loading models, about 10-40 seconds)...
start "Enterprise Fabric AI - API" cmd /k ".venv\Scripts\python.exe -m uvicorn app.api.main:app --port 8000"

set /a tries=0
:wait_api
timeout /t 2 /nobreak >nul
curl -s http://127.0.0.1:8000/api/v1/health >nul 2>&1
if not errorlevel 1 goto api_ready
set /a tries+=1
if %tries% geq 60 (
    echo The API did not start within 2 minutes. Check the API window for errors.
    pause
    exit /b 1
)
goto wait_api

:api_ready
start "Enterprise Fabric AI - Streamlit" cmd /k ".venv\Scripts\python.exe -m streamlit run ui\streamlit_app.py --server.address localhost --browser.gatherUsageStats false"
echo Streamlit is starting: http://localhost:8501
pause
