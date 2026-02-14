@echo off
setlocal

pushd %~dp0\..
set "PYTHONPATH=%CD%"

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

if not defined API_HOST set "API_HOST=127.0.0.1"
if not defined API_PORT (
  set "API_PORT=8000"
  set "API_PORT_WAS_DEFAULT=1"
)

echo [run_api.bat] Starting API on http://%API_HOST%:%API_PORT%
"%PYTHON_EXE%" -m uvicorn threads_poster.main:app --reload --host %API_HOST% --port %API_PORT%
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  if defined API_PORT_WAS_DEFAULT (
    set "API_PORT=8001"
    echo [run_api.bat] Startup failed on port 8000. Retrying on 8001...
    echo [run_api.bat] Starting API on http://%API_HOST%:%API_PORT%
    "%PYTHON_EXE%" -m uvicorn threads_poster.main:app --reload --host %API_HOST% --port %API_PORT%
    set "EXIT_CODE=%ERRORLEVEL%"
  )
)

popd
exit /b %EXIT_CODE%
