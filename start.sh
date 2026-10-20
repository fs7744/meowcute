#!/usr/bin/env bash
# 喵喵叫的可爱助手 · 启动脚本（macOS / Linux / Git Bash）
#
#   ./start.sh            生产模式：前端若没构建就先构建，然后起服务并打开浏览器
#   ./start.sh --dev      开发模式：后端 8788 + Vite 5173（热更新）
#   ./start.sh --setup    只装依赖（核心依赖 + 前端）
#   ./start.sh --gpu      安装 GPU 推理依赖（torch / diffusers，约 5GB）
#   ./start.sh --no-open  不自动开浏览器
#
# 环境变量：
#   PYTHON  指定解释器（默认 python / python3）
#   PORT    服务端口（默认 8788）

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8788}"
DEV=0
NO_OPEN=0
DO_SETUP=0
DO_GPU=0

for a in "$@"; do
  case "$a" in
    --dev) DEV=1 ;;
    --setup) DO_SETUP=1 ;;
    --gpu) DO_GPU=1 ;;
    --no-open) NO_OPEN=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：$a（用 --help 看用法）"; exit 1 ;;
  esac
done

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python >/dev/null 2>&1; then PY=python
  elif command -v python3 >/dev/null 2>&1; then PY=python3
  else echo "没找到 Python，请先装 3.11+ 并确保在 PATH 里"; exit 1; fi
fi

open_url() {
  [ "$NO_OPEN" = "1" ] && return 0
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 || true
  elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$1" >/dev/null 2>&1 || true
  fi
}

install_core() {
  echo "== 安装后端核心依赖 =="
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r backend/requirements.txt
}

install_front() {
  if [ ! -d frontend/node_modules ]; then
    echo "== 安装前端依赖 =="
    ( cd frontend && npm install --no-audit --no-fund )
  fi
}

if [ "$DO_SETUP" = "1" ]; then
  install_core
  install_front
  echo "依赖装好了。GPU 推理依赖另跑：./start.sh --gpu"
  exit 0
fi

if [ "$DO_GPU" = "1" ]; then
  if [ -f backend/install_gpu_deps.sh ]; then
    bash backend/install_gpu_deps.sh
  else
    echo "没找到 backend/install_gpu_deps.sh。手动装："
    echo "  $PY -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124"
    echo "  $PY -m pip install diffusers==0.38.0 transformers==4.57.6 torchao==0.16.0 accelerate"
  fi
  exit 0
fi

install_core
install_front

if [ "$DEV" = "1" ]; then
  echo "== 开发模式：后端 http://127.0.0.1:$PORT ｜ 前端 http://127.0.0.1:5173 =="
  "$PY" -m uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT" --reload &
  BACK=$!
  trap 'kill $BACK 2>/dev/null || true' EXIT INT TERM
  ( cd frontend && npm run dev )
else
  if [ ! -d frontend/dist ]; then
    echo "== 构建前端 =="
    ( cd frontend && npm run build )
  fi
  echo "== 启动服务：http://127.0.0.1:$PORT =="
  open_url "http://127.0.0.1:$PORT"
  exec "$PY" -m uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT"
fi
