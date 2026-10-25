@echo off
setlocal
cd /d "%~dp0"
title Media Library Desktop

if exist "dist\MediaLibraryDesktop.exe" (
  start "" "dist\MediaLibraryDesktop.exe"
  goto :eof
)

if not exist ".venv\Scripts\python.exe" (
  echo Forsta gangen: skapar Python-miljo och installerar paket...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || goto :error
)

".venv\Scripts\python.exe" desktop.py
goto :eof

:error
echo.
echo Kunde inte starta. Kontrollera att Python 3.11+ finns i PATH.
pause
