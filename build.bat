@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   打包 贴吧吧务助手.exe
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [x] 没找到 python，请先安装 Python 3.11+ 并勾选 Add to PATH
    pause
    exit /b 1
)

echo [1/2] 安装依赖...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [x] 依赖安装失败，可尝试换镜像：
    echo     python -m pip install -r requirements.txt pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
    pause
    exit /b 1
)

echo.
echo [2/2] 打包中（首次约 1-2 分钟）...
python -m PyInstaller --noconfirm --clean --onefile ^
    --name "贴吧吧务助手" ^
    --icon icon.ico ^
    --add-data "web;web" ^
    app.py
if errorlevel 1 (
    echo [x] 打包失败，请看上面的报错
    pause
    exit /b 1
)

echo.
echo 完成：dist\贴吧吧务助手.exe
echo 直接把这一个 exe 拷到别的电脑就能用，不需要装 Python。
pause
