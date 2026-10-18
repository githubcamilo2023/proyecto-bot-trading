@echo off
rem Abre el menu guiado de Tradebot (doble clic). No guarda claves: las pide al abrir.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "VENV=%USERPROFILE%\venvs\tradebot"
if not exist "%VENV%\Scripts\activate.bat" (
  echo No encuentro el entorno de Python en: %VENV%
  echo Crealo una sola vez con estos comandos:
  echo   python -m venv "%VENV%"
  echo   "%VENV%\Scripts\activate.bat"
  echo   pip install -c constraints.txt -e ".[dev,llm]"
  pause
  exit /b 1
)
call "%VENV%\Scripts\activate.bat"
python -m tradebot menu
echo.
pause
