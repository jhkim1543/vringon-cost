# -*- coding: utf-8 -*-
"""Generation 도메인 라우터 — 3D 생성 엔진 실호출과 작업 상태.

VRINGON 통합 시 이 모듈이 messaging(SQS) 계약으로 바뀐다:
JOBS dict 가 DynamoDB(작업 상태 원본) 자리, 스레드가 워커 큐 자리다.
"""
import json
import threading
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form

from config import ASSETS
from core.errors import err
from pipeline import Project, safe_pid

router = APIRouter(prefix="/api", tags=["generation"])

# 백그라운드 작업 진행 상황 (3D 생성은 3~5분 걸린다)
JOBS = {}


@router.get("/mesh/balance")
def provider_balance():
    try:
        from mesh_provider import MeshProvider
        return MeshProvider().balance()
    except Exception as e:
        return err(e, "VC-GEN-003")


@router.post("/mesh/generate")
async def provider_generate(image: UploadFile = File(...),
                            project_id: str = Form(...),
                            segment: str = Form("true")):
    """이미지 -> 3D -> (선택) 세그멘테이션. 오래 걸려서 백그라운드로 돈다."""
    try:
        from mesh_provider import MeshProvider
    except Exception as e:
        return err(e, "VC-GEN-003")

    try:
        pid = safe_pid(project_id)
    except Exception as e:
        return err(e, "VC-PROJ-001")

    # 클라이언트 검증은 참고일 뿐이다. 확장자·크기·매직바이트를 서버가 본다.
    ALLOWED = {".jpg": bytes.fromhex("ffd8ff"), ".jpeg": bytes.fromhex("ffd8ff"),
               ".png": bytes.fromhex("89504e47"), ".webp": b"RIFF"}
    MAX_BYTES = 20 * 1024 * 1024
    ext = Path(image.filename or "").suffix.lower()
    if ext not in ALLOWED:
        return err(ValueError("JPG, PNG, WEBP 만 올릴 수 있습니다."), "VC-GEN-001")
    raw = await image.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return err(ValueError("이미지가 20MB 를 넘습니다."), "VC-GEN-002")
    if not raw.startswith(ALLOWED[ext]):
        return err(ValueError("확장자와 실제 파일 형식이 다릅니다."), "VC-GEN-001")

    dst = ASSETS / f"{pid}{ext}"
    dst.write_bytes(raw)

    job = {"project_id": pid, "status": "queued", "progress": 0,
           "stage": "upload", "credits": None, "error": None}
    JOBS[pid] = job

    def run():
        try:
            c = MeshProvider()
            p = Project(pid)
            p.ensure_dir()
            p.state["input_image"] = dst.name
            p.save()

            job.update(stage="generate", status="running")
            res = c.image_to_glb(
                dst, p.dir / "raw_model.glb",
                on_progress=lambda s, pr: job.update(status=s, progress=pr))
            (p.dir / "generate_task.json").write_text(
                json.dumps(res["raw"], ensure_ascii=False, indent=1),
                encoding="utf-8")
            p._mark("generate3d", "done", task_id=res["task_id"])

            if segment.lower() in ("true", "1", "yes"):
                job.update(stage="segment", progress=0)
                seg = c.segment_to_glb(
                    res["task_id"], p.dir / "segmented.glb",
                    on_progress=lambda s, pr: job.update(status=s, progress=pr))
                (p.dir / "segment_task.json").write_text(
                    json.dumps(seg["raw"], ensure_ascii=False, indent=1),
                    encoding="utf-8")
                p._mark("segment3d", "done", task_id=seg["task_id"])

            p.viewer_glb(force=True)
            job.update(stage="done", status="success", progress=100,
                       credits=c.balance())
        except Exception as e:
            job.update(stage="error", status="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return {"project_id": pid, "job": job}


@router.get("/mesh/job/{pid}")
def provider_job(pid: str):
    return JOBS.get(pid, {"status": "unknown"})
