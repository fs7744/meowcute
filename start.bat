@echo off
rem ============================================================
rem  Meowcute launcher (Windows)
rem
rem  FOUR HARD CONSTRAINTS - do not "fix" them:
rem   1) this file must stay CRLF. cmd.exe splits statements on
rem      CRLF; an LF-only .bat makes it glue lines together.
rem   2) this file must stay GBK (cp936), NOT UTF-8. With a UTF-8
rem      .bat cmd eats bytes mid-line and the next line becomes a
rem      bogus command. Hence chcp 936 below and ASCII-only rem
rem      lines. If your editor offers "UTF-8", say no.
rem   3) python is located by RUNNING it, not by "where python".
rem      On many machines where.exe finds the Microsoft Store stub
rem      at WindowsApps\python.exe, which exits with 9009 and is
rem      not a real interpreter at all.
rem   4) interpreter order matters: newer is NOT better here.
rem      torch 2.6 / diffusers 0.38 only ship wheels up to cp313;
rem      3.14+ would fall back to building from source and die
rem      without MSVC, so the script rejects it and tries
rem      3.13 -> 3.12 -> 3.11. It also probes the managed runtime
rem      at %USERPROFILE%\.workbuddy first, and honors set PYTHON=.
rem
rem    start.bat            production: build frontend if needed, serve, open browser
rem    start.bat --dev      dev mode: backend 8788 + Vite 5173
rem    start.bat --setup    install deps only
rem    start.bat --gpu      install GPU inference deps (torch / diffusers)
rem    start.bat --no-open  do not open the browser
rem
rem  Override the interpreter when the auto-detect picks the wrong one:
rem    set PYTHON=C:\path\to\python.exe
rem    start.bat
rem ============================================================
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 喵喵叫的可爱助手

if "%PORT%"=="" set "PORT=8788"
set "DEV=0"
set "NOOPEN=0"
set "PY="
set "FAILED="

rem ---- pick an interpreter ----
rem 顺序：显式 PYTHON > 项目 .venv > 已知托管环境 > python > py -3.13..
if defined PYTHON (
  set "PY=%PYTHON%"
  !PY! -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo [x] PYTHON 指向的解释器跑不起来：%PYTHON%
    pause
    exit /b 1
  )
  goto :py_check_version
)

if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not defined PY (
  if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\pixelforge\Scripts\python.exe" (
    set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\pixelforge\Scripts\python.exe"
  )
)

if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
)
if not defined PY (
  py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
)
if not defined PY (
  py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
)
if not defined PY (
  echo [x] 没找到可用的 Python 3.11-3.13。
  echo.
  echo     本项目需要 3.11 到 3.13。如果已经装了却仍然报这个错，
  echo     多半是 where python 命中了微软商店的占位程序：
  echo       %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe
  echo     解决办法：设置 - 应用 - 高级应用设置 - 应用执行别名，
  echo     把 python.exe / python3.exe 两项关掉，然后重开这个窗口。
  echo.
  echo     也可以直接指定解释器（本机已实测可用）：
  echo       set PYTHON=%USERPROFILE%\.workbuddy\binaries\python\envs\pixelforge\Scripts\python.exe
  echo       start.bat
  pause
  exit /b 1
)

:py_check_version
rem ---- 版本门槛：3.14+ 没有预编译包，源码编译必失败，直接拦下 ----
set "PYVER=?"
set "PYMIN=0"
for /f "tokens=1,2" %%a in ('!PY! -c "import sys;print(sys.version_info[0],sys.version_info[1])" 2^>nul') do (
  set "PYMAJ=%%a"
  set "PYMIN=%%b"
  set "PYVER=%%a.%%b"
)
if !PYMIN! GTR 13 (
  echo [x] 当前解释器是 Python !PYVER!，太新了。
  echo     torch 2.6 / diffusers 0.38 只提供到 3.13 的预编译包，
  echo     3.14 以上 pip 会尝试源码编译，没装 MSVC 就失败。
  echo.
  echo     请指定一个 3.11-3.13 的解释器：
  echo       set PYTHON=C:/Python312/python.exe
  echo       start.bat
  pause
  exit /b 1
)


rem ---- pip 真的能用吗（嵌入式发行版常常没有 pip） ----
!PY! -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [x] 这个 Python 没有可用的 pip：!PY!
  echo     手动装一下再重跑：
  echo     !PY! -m ensurepip --upgrade
  pause
  exit /b 1
)

:parse
if "%~1"=="" goto start
if /i "%~1"=="--dev" ( set "DEV=1" & shift & goto parse )
if /i "%~1"=="--no-open" ( set "NOOPEN=1" & shift & goto parse )
if /i "%~1"=="--setup" goto setup
if /i "%~1"=="--gpu" goto gpu
if /i "%~1"=="--help" goto help
echo 未知参数：%~1
echo 用 start.bat --help 看用法
exit /b 1

:help
echo   start.bat            生产模式：构建前端、起服务、开浏览器
echo   start.bat --dev      开发模式：后端 8788 加 Vite 5173 热更新
echo   start.bat --setup    只装依赖
echo   start.bat --gpu      安装 GPU 推理依赖
echo   start.bat --no-open  不自动开浏览器
echo.
echo   解释器：!PY!
echo   版本  ：!PYVER!
exit /b 0

:setup
call :deps
if errorlevel 1 exit /b 1
echo.
echo 依赖装好了。GPU 推理依赖另外跑 start.bat --gpu
exit /b 0

:gpu
echo == 安装 GPU 推理依赖，约 5GB，会比较慢 ==
!PY! -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 goto gpu_fail
!PY! -m pip install diffusers==0.38.0 transformers==4.57.6 torchao==0.16.0 accelerate sentencepiece protobuf
if errorlevel 1 goto gpu_fail
echo 装好了。接着跑 start.bat --setup 检查核心依赖。
exit /b 0

:gpu_fail
echo [x] GPU 依赖安装失败。先确认显卡驱动和 CUDA 版本，再重跑 start.bat --gpu
pause
exit /b 1

:start
call :deps
if errorlevel 1 exit /b 1

if "!DEV!"=="1" (
  echo == 开发模式 ==
  echo    后端 http://127.0.0.1:!PORT!
  echo    前端 http://127.0.0.1:5173
  start "meowcute-backend" cmd /c "!PY! -m uvicorn backend.app.main:app --host 127.0.0.1 --port !PORT! --reload"
  pushd frontend
  call npm run dev
  popd
  exit /b 0
)

if not exist "frontend\dist" (
  echo == 构建前端 ==
  pushd frontend
  call npm run build
  if errorlevel 1 set "FAILED=1"
  popd
)
rem 注意：errorlevel 必须紧跟被检查的命令，pushd/popd 会把它覆盖掉
if defined FAILED (
  echo [x] 前端构建失败。手动跑一次看完整报错：
  echo     cd frontend
  echo     npm install
  echo     npm run build
  pause
  exit /b 1
)

echo == 服务地址 http://127.0.0.1:!PORT! ==
echo    按 Ctrl+C 停止。
if "!NOOPEN!"=="0" start "" "http://127.0.0.1:!PORT!"
!PY! -m uvicorn backend.app.main:app --host 127.0.0.1 --port !PORT!
if errorlevel 1 pause
exit /b 0

rem ---- install core deps ----
:deps
echo == 检查后端依赖：!PY! ^(Python !PYVER!^) ==
!PY! -m pip install -q --upgrade pip >nul 2>&1
!PY! -m pip install -q -r backend\requirements.txt
if errorlevel 1 (
  echo [x] 后端核心依赖安装失败。常见原因：
  echo     1. Python 版本太新，没有预编译包（建议 3.11 到 3.13）
  echo     2. 网络不通，换国内镜像试试：
  echo        !PY! -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  echo     3. 想换解释器：set PYTHON=C:\path\to\python.exe 再重跑
  echo.
  echo     原始命令（去掉 -q 可看完整报错）：
  echo        !PY! -m pip install -r backend\requirements.txt
  pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [x] 没找到 npm。先装 Node.js 18 以上版本，装完重新打开窗口。
  pause
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo == 安装前端依赖 ==
  pushd frontend
  call npm install --no-audit --no-fund
  if errorlevel 1 set "FAILED=1"
  popd
)
if defined FAILED (
  echo [x] 前端依赖安装失败。手动跑：cd frontend 然后 npm install
  pause
  exit /b 1
)
exit /b 0
