@echo off
setlocal
cd /d "%~dp0"
title Media Library Desktop (kallkod)

if not exist ".venv\Scripts\python.exe" (
  echo Forsta gangen: skapar Python-miljo och installerar paket...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || goto :error
)

rem Kor alltid kallkoden - inte den byggda exe-filen.
".venv\Scripts\python.exe" desktop.py
if errorlevel 1 pause
goto :eof

:error
echo.
echo Kunde inte starta. Kontrollera att Python 3.11+ finns i PATH.
pause
