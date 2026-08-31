# -*- coding: utf-8 -*-
"""Material 도메인 라우터 — 카탈로그와 파트별 소재 선택·승인."""
from fastapi import APIRouter

import canonical
import catalog
from config import provider_api_key
from core.errors import err
from pipeline import Project

router = APIRouter(prefix="/api", tags=["material"])


@router.get("/catalog")
def get_catalog():
    return {
        "canonical_parts": sorted(catalog.bom_master()),
        "signature_parts": sorted(canonical.SIGNATURES),
        "bom_master": catalog.bom_master(),
        "material_specs": catalog.material_specs(),
        "part_defaults": catalog.part_defaults(),
        "recipes": catalog.recipes(),
        "routing": catalog.routing(),
        "tooling": catalog.tooling(),
        "quarters": sorted({q for (q, _s) in catalog.quarterly_prices()}),
        "part_material_map": catalog.part_material_map(),
        "mesh_provider_key_present": bool(provider_api_key()),
    }


@router.get("/project/{pid}/materials")
def get_materials(pid: str):
    """파트별 소재 후보와 현재 선택."""
    try:
        p = Project(pid)
        return {"selected": p.state.get("materials") or {},
                "options": p.material_options(),
                "defaults": catalog.part_defaults()}
    except Exception as e:
        return err(e)


@router.post("/project/{pid}/materials")
def post_materials(pid: str, payload: dict):
    """파트 또는 세그먼트별 소재를 승인한다. {"Vamp": "MAT-FULLGRAIN"}"""
    try:
        p = Project(pid)
        return {"materials": p.set_materials(payload or {})}
    except ValueError as e:
        code = "VC-MAT-002" if "분기 단가" in str(e) else "VC-MAT-001"
        return err(e, code)
    except Exception as e:
        return err(e)
