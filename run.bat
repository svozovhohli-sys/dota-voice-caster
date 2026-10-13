@echo off
REM Windows launcher: creates venv, installs deps, runs the app.
cd /d "%~dp0"

if not exist .venv (
  echo Создаю виртуальное окружение...
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python main.py %*
