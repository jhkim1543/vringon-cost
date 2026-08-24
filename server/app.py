# -*- coding: utf-8 -*-
"""FastAPI 서버.

    .venv\\Scripts\\python.exe server\\app.py     ->  http://127.0.0.1:5270

엔드포인트는 계획서 §16을 따른다.
"""
import json
import os
import sys
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import catalog
import canonical
import pricing
from config import WEB, STORE, ASSETS, DATA, provider_api_key
from pipeline import Project, import_mesh_provider_outputs

app = FastAPI(title="VRINGON Cost — 신발 Design-to-Should-Cost")

# 백그라운드 작업 진행 상황 (3D 생성은 3~5분 걸린다)
JOBS = {}


def _err(e):
    return JSONResponse(status_code=400,
                        content={"error": str(e), "trace": traceback.format_exc()[-1200:]})


# ── 카탈로그 ──────────────────────────────────────────────────────────
@app.get("/api/catalog")
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


@app.get("/api/prices/{quarter}")
def get_prices(quarter: str):
    rows = [v for (q, _s), v in catalog.quarterly_prices().items() if q == quarter]
    return {"quarter": quarter, "rows": sorted(rows, key=lambda r: r["spec_id"])}


@app.post("/api/prices/snapshot")
def post_snapshot(payload: dict):
    """다음 분기 스냅샷 생성. 신규 관측이 없으면 stale 이관되는지 보여준다."""
    return pricing.make_snapshot(payload.get("quarter", "2026Q4"),
                                 payload.get("observations"))


# ── 예시 디자인 ────────────────────────────────────────────────────────
@app.get("/api/examples")
def get_examples():
    """예시 디자인 목록. 각 항목은 미리 계산된 프로젝트에 연결된다."""
    f = DATA / "examples" / "examples.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    for ex in d["examples"]:
        ex["ready"] = (STORE / ex["project"] / "cost.json").exists()
    return d


@app.get("/api/examples/{name}")
def get_example_image(name: str):
    p = (DATA / "examples" / name).resolve()
    if not str(p).startswith(str((DATA / "examples").resolve())) or not p.exists():
        raise HTTPException(404, "예시 이미지 없음")
    return FileResponse(p)


# ── 프로젝트 ──────────────────────────────────────────────────────────
@app.get("/api/projects")
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


@app.get("/api/project/{pid}")
def get_project(pid: str):
    p = Project(pid)
    # 예전 상태 파일에는 요약이 없다. 읽을 때 채워준다.
    if p.state.get("mapping") and not p.state.get("mapping_summary"):
        from pipeline import mapping_summary
        p.state["mapping_summary"] = mapping_summary(p.state["mapping"])
        p.save()
    return p.state


@app.get("/api/project/{pid}/model.glb")
def get_model(pid: str):
    p = Project(pid)
    try:
        return FileResponse(p.viewer_glb(), media_type="model/gltf-binary")
    except Exception as e:
        raise HTTPException(404, str(e))


@app.get("/api/project/{pid}/image")
def get_image(pid: str):
    p = Project(pid)
    name = p.state.get("input_image")
    if not name or not (ASSETS / name).exists():
        raise HTTPException(404, "입력 이미지 없음")
    return FileResponse(ASSETS / name)


# ── 파이프라인 단계 ────────────────────────────────────────────────────
@app.post("/api/project/{pid}/landmarks")
def post_landmarks(pid: str):
    try:
        return Project(pid).propose_landmarks()
    except Exception as e:
        return _err(e)


@app.post("/api/project/{pid}/calibrate")
def post_calibrate(pid: str, payload: dict):
    try:
        p = Project(pid)
        return p.calibrate(float(payload["target_length_mm"]),
                           toe=payload.get("toe"), heel=payload.get("heel"),
                           confirmed=bool(payload.get("confirmed")))
    except Exception as e:
        return _err(e)


@app.post("/api/project/{pid}/segment/propose")
def post_seg_propose(pid: str):
    try:
        return {"mapping": Project(pid).propose_mapping()}
    except Exception as e:
        return _err(e)


@app.post("/api/project/{pid}/segment/confirm")
def post_seg_confirm(pid: str, payload: dict):
    try:
        p = Project(pid)
        return {"mapping": p.confirm_mapping(overrides=payload.get("overrides"),
                                             confirm_all=bool(payload.get("confirm_all")))}
    except Exception as e:
        return _err(e)


@app.post("/api/project/{pid}/repair")
def post_repair(pid: str, payload: dict = None):
    try:
        p = Project(pid)
        return {"repairs": p.repair_volumes((payload or {}).get("segment_ids"))}
    except Exception as e:
        return _err(e)


@app.post("/api/project/{pid}/bom")
def post_bom(pid: str, payload: dict = None):
    try:
        p = Project(pid)
        return {"bom": p.build_bom(flags=(payload or {}).get("flags"))}
    except Exception as e:
        return _err(e)


@app.post("/api/project/{pid}/scenario")
def post_scenario(pid: str, payload: dict):
    p = Project(pid)
    p.state["scenario"].update(payload or {})
    p.save()
    return p.state["scenario"]


@app.post("/api/project/{pid}/gates")
def post_gates(pid: str, payload: dict):
    """엔지니어 승인 게이트를 켜고 끈다 (계획서 §12)."""
    p = Project(pid)
    p.state.setdefault("gates", {}).update(payload or {})
    p.save()
    return p.state["gates"]


@app.post("/api/project/{pid}/cost")
def post_cost(pid: str):
    try:
        return Project(pid).estimate()
    except Exception as e:
        return _err(e)


@app.get("/api/project/{pid}/cost")
def get_cost(pid: str):
    f = Project(pid).dir / "cost.json"
    if not f.exists():
        raise HTTPException(404, "아직 계산되지 않았습니다")
    return json.loads(f.read_text(encoding="utf-8"))


# ── 3D 생성 엔진 실호출 ──────────────────────────────────────────────────────
@app.get("/api/mesh/balance")
def provider_balance():
    try:
        from mesh_provider import MeshProvider
        return MeshProvider().balance()
    except Exception as e:
        return _err(e)


@app.post("/api/mesh/generate")
async def provider_generate(image: UploadFile = File(...),
                         project_id: str = Form(...),
                         segment: str = Form("true")):
    """이미지 -> 3D -> (선택) 세그멘테이션. 오래 걸려서 백그라운드로 돈다."""
    try:
        from mesh_provider import MeshProvider
    except Exception as e:
        return _err(e)

    pid = project_id.strip() or "RUN"
    dst = ASSETS / f"{pid}{Path(image.filename).suffix.lower() or '.jpg'}"
    dst.write_bytes(await image.read())

    job = {"project_id": pid, "status": "queued", "progress": 0,
           "stage": "upload", "credits": None, "error": None}
    JOBS[pid] = job

    def run():
        try:
            c = MeshProvider()
            p = Project(pid)
            p.state["input_image"] = dst.name
            p.save()

            job.update(stage="generate", status="running")
            res = c.image_to_glb(
                dst, p.dir / "raw_model.glb",
                on_progress=lambda s, pr: job.update(status=s, progress=pr))
            (p.dir / "generate_task.json").write_text(
                json.dumps(res["raw"], ensure_ascii=False, indent=1), encoding="utf-8")
            p._mark("generate3d", "done", task_id=res["task_id"])

            if segment.lower() in ("true", "1", "yes"):
                job.update(stage="segment", progress=0)
                seg = c.segment_to_glb(
                    res["task_id"], p.dir / "segmented.glb",
                    on_progress=lambda s, pr: job.update(status=s, progress=pr))
                (p.dir / "segment_task.json").write_text(
                    json.dumps(seg["raw"], ensure_ascii=False, indent=1), encoding="utf-8")
                p._mark("segment3d", "done", task_id=seg["task_id"])

            p.viewer_glb(force=True)
            job.update(stage="done", status="success", progress=100,
                       credits=c.balance())
        except Exception as e:
            job.update(stage="error", status="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return {"project_id": pid, "job": job}


@app.get("/api/mesh/job/{pid}")
def provider_job(pid: str):
    return JOBS.get(pid, {"status": "unknown"})


# ── 정적 ──────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5270))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
