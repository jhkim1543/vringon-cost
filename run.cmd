@echo off
REM VRINGON Cost 데모 서버.  http://127.0.0.1:5270
REM MESH_API_KEY 는 여기에 쓰지 말 것. 환경변수 또는 ..\scripts\run_backend.cmd 에서 읽는다.
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo [!] .venv 가 없습니다. 먼저 아래를 실행하세요:
  echo     python -m venv --system-site-packages .venv
  echo     .venv\Scripts\python.exe -m pip install fastapi uvicorn python-multipart manifold3d pytest
  exit /b 1
)
if not exist data\seed\_manifest.json (
  echo [i] 워크북 시드를 생성합니다...
  .venv\Scripts\python.exe tools\seed_from_xlsx.py
)
.venv\Scripts\python.exe server\app.py
