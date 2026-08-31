# -*- coding: utf-8 -*-
"""Segmentation 도메인 라우터 — 매핑 제안과 확정."""
from fastapi import APIRouter

from core.errors import err
from pipeline import Project

router = APIRouter(prefix="/api", tags=["segmentation"])


@router.post("/project/{pid}/segment/propose")
def post_seg_propose(pid: str):
    try:
        return {"mapping": Project(pid).propose_mapping()}
    except FileNotFoundError as e:
        return err(e, "VC-GEO-001", 404)
    except Exception as e:
        return err(e)


@router.post("/project/{pid}/segment/confirm")
def post_seg_confirm(pid: str, payload: dict):
    try:
        p = Project(pid)
        return {"mapping": p.confirm_mapping(
            overrides=payload.get("overrides"),
            confirm_all=bool(payload.get("confirm_all")))}
    except FileNotFoundError as e:
        return err(e, "VC-GEO-001", 404)
    except Exception as e:
        return err(e)
