@echo off
echo ========================================
echo Unsloth-to-Ollama Proxy Launcher
echo ========================================
echo.

REM Check if uv is installed
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo uv not found. Installing uv...
    powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    echo uv installed successfully.
    echo.
    REM Refresh PATH for current session after uv install
    SET "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

REM Check if .venv exists
if not exist ".venv" (
    echo Creating UV environment...
    uv venv .venv
    echo UV environment created.
    echo.
) else (
    echo UV environment already exists.
    echo.
)

REM Install/update dependencies
echo Installing/updating dependencies from requirements.txt...
uv pip install -r requirements.txt
echo Dependencies installed successfully.
echo.

REM Launch the application
echo Starting Unsloth-to-Ollama Proxy...
echo Server will run on http://localhost:11434
echo.
uv run python main.py