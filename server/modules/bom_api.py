# -*- coding: utf-8 -*-
"""BOM 도메인 라우터 — 제조 BOM 생성과 엔지니어 승인."""
from fastapi import APIRouter

from core.errors import err
from pipeline import Project

router = APIRouter(prefix="/api", tags=["bom"])


@router.post("/project/{pid}/bom/approve")
def post_bom_approve(pid: str, payload: dict):
    """규칙 제안 BOM 라인을 승인한다. {"actor": "...", "evidence": "..."}"""
    try:
        p = Project(pid)
        return p.approve_bom(payload.get("actor"), payload.get("evidence"),
                             payload.get("line_ids"))
    except ValueError as e:
        code = "VC-BOM-002" if "승인자" in str(e) else "VC-BOM-001"
        return err(e, code)
    except Exception as e:
        return err(e)


@router.post("/project/{pid}/bom")
def post_bom(pid: str, payload: dict = None):
    try:
        p = Project(pid)
        return {"bom": p.build_bom(flags=(payload or {}).get("flags"))}
    except FileNotFoundError as e:
        return err(e, "VC-GEO-001", 404)
    except Exception as e:
        return err(e)
