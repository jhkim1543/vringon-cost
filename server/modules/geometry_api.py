# -*- coding: utf-8 -*-
"""Geometry 도메인 라우터 — 뷰어 GLB, landmark, 캘리브레이션, 부피 복구."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core.errors import err
from pipeline import Project

router = APIRouter(prefix="/api", tags=["geometry"])


@router.get("/project/{pid}/model.glb")
def get_model(pid: str):
    try:
        p = Project(pid)
    except ValueError as e:
        return err(e, "VC-PROJ-001")
    try:
        return FileResponse(p.viewer_glb(), media_type="model/gltf-binary")
    except Exception as e:
        raise HTTPException(404, str(e))


@router.post("/project/{pid}/landmarks")
def post_landmarks(pid: str):
    try:
        return Project(pid).propose_landmarks()
    except FileNotFoundError as e:
        return err(e, "VC-GEO-001", 404)
    except Exception as e:
        return err(e)


@router.post("/project/{pid}/calibrate")
def post_calibrate(pid: str, payload: dict):
    try:
        p = Project(pid)
        return p.calibrate(float(payload["target_length_mm"]),
                           toe=payload.get("toe"), heel=payload.get("heel"),
                           confirmed=bool(payload.get("confirmed")))
    except FileNotFoundError as e:
        return err(e, "VC-GEO-001", 404)
    except Exception as e:
        return err(e)


@router.post("/project/{pid}/repair")
def post_repair(pid: str, payload: dict = None):
    try:
        p = Project(pid)
        # 완성본이 없으면 먼저 만들어 R3 경로를 연다 (크레딧 소모, 실패해도
        # R4 복셀 fallback 으로 계속 간다).
        try:
            p.ensure_completed()
        except Exception:
            pass
        return {"repairs": p.repair_volumes((payload or {}).get("segment_ids"))}
    except ValueError as e:
        return err(e, "VC-GEO-003")
    except FileNotFoundError as e:
        return err(e, "VC-GEO-001", 404)
    except Exception as e:
        return err(e)
