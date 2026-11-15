@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] checking dependencies...
python -c "import aiotieba" >nul 2>nul
if errorlevel 1 (
  echo     aiotieba not found, installing...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo     install failed. try:
    echo     python -m pip install aiotieba -i https://mirrors.aliyun.com/pypi/simple/
    pause
    exit /b 1
  )
)

echo [2/2] starting http://127.0.0.1:8765/
python app.py
pause
