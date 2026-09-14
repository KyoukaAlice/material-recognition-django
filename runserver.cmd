@echo off
setlocal
cd /d "%~dp0"
title 材料识别系统

set "PY=.venv\Scripts\python.exe"

REM ---------- 1) 准备虚拟环境 ----------
if exist "%PY%" goto :deps

echo [1/4] 创建虚拟环境 .venv ...
where python >nul 2>nul
if errorlevel 1 goto :nopython
python -m venv .venv
if errorlevel 1 goto :error

:deps
REM ---------- 2) 安装依赖 ----------
echo [2/4] 检查依赖（首次约 250 MB，需等待几分钟）...
"%PY%" -m pip install -q --upgrade pip
"%PY%" -m pip install -q -r requirements.txt
if errorlevel 1 goto :error

REM ---------- 3) 建表 ----------
echo [3/4] 初始化数据库...
"%PY%" manage.py migrate
if errorlevel 1 goto :error

REM ---------- 4) 导入原 MySQL 数据（可重复执行）----------
echo [4/4] 导入原 MySQL 数据...
"%PY%" manage.py import_legacy_data

echo.
echo ============================================================
echo   服务地址 : http://127.0.0.1:8000
echo   管理员   : Admin / 123456
echo   停止服务 : 在本窗口按 Ctrl+C
echo ============================================================
echo.
"%PY%" manage.py runserver 127.0.0.1:8000
goto :end

:nopython
echo.
echo [错误] 没有找到 python 命令。
echo        请先安装 Python 3.9 或更高版本，安装时务必勾选
echo        "Add Python to PATH"，然后重新运行本脚本。
echo        下载地址: https://www.python.org/downloads/
goto :end

:error
echo.
echo [错误] 启动失败，请把上面的输出内容发给开发者。

:end
echo.
pause
