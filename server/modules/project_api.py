# -*- coding: utf-8 -*-
"""Project 도메인 라우터 — 프로젝트 목록·상태·입력 이미지·시나리오·예시.

Service 역할은 pipeline.Project 가 맡는다 (BE 의 Controller/Service 분리에서
Service 가 엔진 쪽에 이미 있는 형태).
"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import STORE, ASSETS, DATA
from core.errors import err
from pipeline import Project, mapping_summary

router = APIRouter(prefix="/api", tags=["project"])


@router.get("/projects")
def list_projects():
    out = []
    for d in sorted(STORE.iterdir()):
        f = d / "state.json"
        if f.exists():
            s = json.loads(f.read_text(encoding="utf-8"))
            out.append({"project_id": s["project_id"], "steps": s.get("steps", {}),
                        "updated_at": s.get("updated_at"),
                        "grade": (s.get("cost") or {}).get("grade", {}).get("class")})
    return {"projects": out}


@router.get("/project/{pid}")
def get_project(pid: str):
    try:
        p = Project(pid)
    except ValueError as e:
        return err(e, "VC-PROJ-001")
    # 예전 상태 파일에는 요약이 없다. 읽을 때 채워준다.
    if p.state.get("mapping") and not p.state.get("mapping_summary"):
        p.state["mapping_summary"] = mapping_summary(p.state["mapping"])
        p.save()
    return p.state


@router.get("/project/{pid}/image")
def get_image(pid: str):
    try:
        p = Project(pid)
    except ValueError as e:
        return err(e, "VC-PROJ-001")
    name = p.state.get("input_image")
    if not name or not (ASSETS / name).exists():
        raise HTTPException(404, "입력 이미지 없음")
    return FileResponse(ASSETS / name)


@router.post("/project/{pid}/scenario")
def post_scenario(pid: str, payload: dict):
    try:
        p = Project(pid)
    except ValueError as e:
        return err(e, "VC-PROJ-001")
    p.state["scenario"].update(payload or {})
    p.save()
    return p.state["scenario"]


@router.get("/examples")
def get_examples():
    """예시 디자인 목록. 각 항목은 미리 계산된 프로젝트에 연결된다."""
    f = DATA / "examples" / "examples.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    for ex in d["examples"]:
        ex["ready"] = (STORE / ex["project"] / "cost.json").exists()
    return d


@router.get("/examples/{name}")
def get_example_image(name: str):
    p = (DATA / "examples" / name).resolve()
    if not str(p).startswith(str((DATA / "examples").resolve())) or not p.exists():
        raise HTTPException(404, "예시 이미지 없음")
    return FileResponse(p)
