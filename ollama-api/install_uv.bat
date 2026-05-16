@echo off
echo Installing uv...
powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
echo.
echo uv installation complete!
echo.
pause