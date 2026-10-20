@echo off
rem 喵喵叫的可爱助手 · 启动脚本（Windows）
rem
rem   start.bat            生产模式：前端没构建就先构建，然后起服务并打开浏览器
rem   start.bat --dev      开发模式：后端 8788 + Vite 5173（热更新）
rem   start.bat --setup    只装依赖
rem   start.bat --gpu      安装 GPU 推理依赖（torch / diffusers）
rem   start.bat --no-open  不自动开浏览器

setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%PORT%"=="" set PORT=8788
set DEV=0
set NOOPEN=0

:parse
if "%~1"=="" goto start
if /i "%~1"=="--dev" ( set DEV=1 & shift & goto parse )
if /i "%~1"=="--no-open" ( set NOOPEN=1 & shift & goto parse )
if /i "%~1"=="--setup" goto setup
if /i "%~1"=="--gpu" goto gpu
if /i "%~1"=="--help" goto help
echo 未知参数：%~1（用 --help 看用法）
exit /b 1

:help
echo start.bat            生产模式
echo start.bat --dev      开发模式（Vite 热更新）
echo start.bat --setup    只装依赖
echo start.bat --gpu      安装 GPU 推理依赖
exit /b 0

:setup
call :deps
echo 依赖装好了。GPU 推理依赖另跑：start.bat --gpu
exit /b 0

:gpu
echo == 安装 GPU 推理依赖 ==
python -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
python -m pip install diffusers==0.38.0 transformers==4.57.6 torchao==0.16.0 accelerate sentencepiece protobuf
exit /b 0

:start
call :deps

if "%DEV%"=="1" (
  echo == 开发模式：后端 http://127.0.0.1:%PORT%  前端 http://127.0.0.1:5173 ==
  start "meowcute-backend" cmd /c python -m uvicorn backend.app.main:app --host 127.0.0.1 --port %PORT% --reload
  cd frontend
  call npm run dev
  exit /b 0
)

if not exist "frontend\dist" (
  echo == 构建前端 ==
  pushd frontend
  call npm run build
  popd
)

echo == 启动服务：http://127.0.0.1:%PORT% ==
if "%NOOPEN%"=="0" start "" "http://127.0.0.1:%PORT%"
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port %PORT%
exit /b 0

:deps
echo == 检查后端依赖 ==
python -m pip install -q --upgrade pip
python -m pip install -q -r backend\requirements.txt
if not exist "frontend\node_modules" (
  echo == 安装前端依赖 ==
  pushd frontend
  call npm install --no-audit --no-fund
  popd
)
exit /b 0
