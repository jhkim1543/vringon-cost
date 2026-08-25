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

# 공개 정적 페이지(GitHub Pages)가 이 백엔드를 부를 수 있게 허용한다.
# 추가 origin 은 환경변수 CORS_ORIGINS (쉼표 구분) 로 넣는다.
from fastapi.middleware.cors import CORSMiddleware
_origins = ["https://jhkim1543.github.io", "http://localhost:5270",
            "http://127.0.0.1:5270"]
_origins += [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",")
             if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["*"], allow_headers=["*"])

# 백그라운드 작업 진행 상황 (3D 생성은 3~5분 걸린다)
JOBS = {}


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dev_mode():
    """EB 는 PORT 를 준다. 운영에서는 내부 정보를 내보내지 않는다."""
    return os.environ.get("ALLOW_DEBUG") == "1" or not os.environ.get("PORT")


def _err(e):
    body = {"error": str(e)}
    # 스택 트레이스에는 서버 경로와 내부 구조가 그대로 담긴다.
    if _dev_mode():
        body["trace"] = traceback.format_exc()[-1200:]
    return JSONResponse(status_code=400, content=body)


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
        # 완성본이 없으면 먼저 만들어 R3 경로를 연다 (크레딧 소모, 실패해도
        # R4 복셀 fallback 으로 계속 간다).
        try:
            p.ensure_completed()
        except Exception:
            pass
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
    """엔지니어 승인 게이트를 켜고 끈다 (계획서 §12).

    게이트는 등급(C1/C2)을 좌우한다. 참/거짓만 받아서 바꾸면 누가 무슨
    근거로 올렸는지가 남지 않아, 승인 기록이 아니라 그냥 플래그가 된다.
    그래서 승인자와 근거를 함께 요구하고 변경 이력을 남긴다.
    """
    from config import CLASS_REQUIREMENTS
    try:
        p = Project(pid)
        known = {k for reqs in CLASS_REQUIREMENTS.values() for k, _ in reqs}
        gates = p.state.setdefault("gates", {})
        log = p.state.setdefault("gate_log", [])
        changed = {}
        for key, val in (payload or {}).items():
            if key not in known:
                raise ValueError(f"알 수 없는 게이트: {key}")
            if not isinstance(val, dict):
                raise ValueError(
                    f"{key}: 승인자와 근거가 필요합니다 "
                    '{"value": true, "actor": "...", "evidence": "..."}')
            actor = str(val.get("actor") or "").strip()
            evidence = str(val.get("evidence") or "").strip()
            if not actor or not evidence:
                raise ValueError(f"{key}: actor 와 evidence 는 비울 수 없습니다")
            before = gates.get(key)
            gates[key] = bool(val.get("value"))
            changed[key] = gates[key]
            log.append({"gate": key, "from": before, "to": gates[key],
                        "actor": actor, "evidence": evidence,
                        "note": val.get("note"), "at": _now_iso()})
        p.save()
        return {"gates": gates, "changed": changed, "log_entries": len(log)}
    except Exception as e:
        return _err(e)


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

    try:
        from pipeline import safe_pid
        pid = safe_pid(project_id)
    except Exception as e:
        return _err(e)

    # 클라이언트 검증은 참고일 뿐이다. 확장자·크기·매직바이트를 서버가 본다.
    ALLOWED = {".jpg": bytes.fromhex("ffd8ff"), ".jpeg": bytes.fromhex("ffd8ff"),
               ".png": bytes.fromhex("89504e47"), ".webp": b"RIFF"}
    MAX_BYTES = 20 * 1024 * 1024
    ext = Path(image.filename or "").suffix.lower()
    if ext not in ALLOWED:
        return _err(ValueError("JPG, PNG, WEBP 만 올릴 수 있습니다."))
    raw = await image.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return _err(ValueError("이미지가 20MB 를 넘습니다."))
    if not raw.startswith(ALLOWED[ext]):
        return _err(ValueError("확장자와 실제 파일 형식이 다릅니다."))

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


# ── 개발용 뷰어 캡처 ───────────────────────────────────────────────────
@app.post("/api/debug/capture")
def debug_capture(payload: dict):
    if os.environ.get("ALLOW_DEBUG") != "1" and os.environ.get("PORT"):
        # EB 는 PORT 를 준다. 운영에서는 이 통로를 닫는다.
        raise HTTPException(404, "운영에서는 비활성")
    """브라우저가 실제로 그린 프레임을 파일로 받는다.

    뷰어 렌더 문제는 서버 쪽 기하 측정만으로는 판정이 안 된다.
    화면 픽셀을 그대로 받아서 눈으로 확인하기 위한 로컬 전용 통로다.
    """
    import base64
    d = payload.get("data_url", "")
    if "," not in d:
        raise HTTPException(400, "data URL 형식이 아님")
    import re as _re
    raw = str(payload.get("name", "capture"))
    name = _re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:64] + ".png"
    out = DATA / "debug" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(d.split(",", 1)[1]))
    return {"saved": str(out), "bytes": out.stat().st_size}


# ── 정적 ──────────────────────────────────────────────────────────────
# 개발 중에는 정적 자산을 캐시하지 않는다. 캐시된 옛 스크립트를 보고
# "고쳤는데 그대로다" 로 오판하는 일을 막는다.
@app.middleware("http")
async def _no_cache(request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5270))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
