@echo off
REM Enterprise Fabric AI - start the demo (React UI served by the FastAPI backend).
REM Opens one "Enterprise Fabric AI - API" window and your browser at http://127.0.0.1:8000.
REM Close that window (or press Ctrl+C in it) to stop.
REM The old Streamlit UI is still available: scripts\run_demo_streamlit.bat
REM Note: local Qdrant allows one program at a time, so do not run this while
REM scripts\ask.py, the indexer or an evaluation is running.

cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found in %CD%\.venv
    pause
    exit /b 1
)

REM Models are already downloaded: load them from the local cache, quietly.
set HF_HUB_OFFLINE=1
set HF_HUB_VERBOSITY=error
set HF_HUB_DISABLE_PROGRESS_BARS=1
set TRANSFORMERS_VERBOSITY=error

REM Stop early with a clear message if another program is using the search index.
.venv\Scripts\python.exe -m scripts.check_index_free
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

REM Build the React UI once if it has not been built yet (needs Node.js).
if not exist "frontend\dist\index.html" (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo The React UI has not been built and Node.js/npm was not found.
        echo Install Node.js LTS, or use scripts\run_demo_streamlit.bat instead.
        pause
        exit /b 1
    )
    echo Building the React UI for the first time...
    pushd frontend
    call npm ci
    call npm run build
    popd
    if not exist "frontend\dist\index.html" (
        echo The UI build failed. See the messages above.
        pause
        exit /b 1
    )
)

REM Sign-in: the demo users need DEMO_PASSWORD in .env (see .env.example).
findstr /b /c:"DEMO_PASSWORD=" .env >nul 2>&1
if errorlevel 1 (
    echo NOTE: DEMO_PASSWORD is not set in .env, so the demo users cannot sign in yet.
    echo       Add a line such as DEMO_PASSWORD=your-choice to .env and run this again,
    echo       or use "Create account" on the sign-in screen.
    echo.
)

echo Starting Enterprise Fabric AI (loading models, about 10-40 seconds)...
start "Enterprise Fabric AI - API" cmd /k ".venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000"

set /a tries=0
:wait_api
timeout /t 2 /nobreak >nul
curl -s http://127.0.0.1:8000/api/v1/health >nul 2>&1
if not errorlevel 1 goto api_ready
set /a tries+=1
if %tries% geq 60 (
    echo The API did not start within 2 minutes. Check the "Enterprise Fabric AI - API" window.
    pause
    exit /b 1
)
goto wait_api

:api_ready
echo Ready: http://127.0.0.1:8000  (API docs: http://127.0.0.1:8000/docs)
start "" http://127.0.0.1:8000
echo You can close this window; the app keeps running in the API window.
pause
