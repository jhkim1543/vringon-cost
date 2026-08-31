# -*- coding: utf-8 -*-
"""Costing 도메인 라우터 — 원가 계산·조회·승인 게이트."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from core.errors import err
from pipeline import Project

router = APIRouter(prefix="/api", tags=["costing"])


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.post("/project/{pid}/gates")
def post_gates(pid: str, payload: dict):
    """엔지니어 승인 게이트를 켜고 끈다 (계획서 §12).

    게이트는 등급(C1~C4)을 좌우한다. 참/거짓만 받아서 바꾸면 누가 무슨
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
                return err(ValueError(f"알 수 없는 게이트: {key}"), "VC-GATE-001")
            if not isinstance(val, dict):
                return err(ValueError(
                    f"{key}: 승인자와 근거가 필요합니다 "
                    '{"value": true, "actor": "...", "evidence": "..."}'),
                    "VC-GATE-002")
            actor = str(val.get("actor") or "").strip()
            evidence = str(val.get("evidence") or "").strip()
            if not actor or not evidence:
                return err(ValueError(
                    f"{key}: actor 와 evidence 는 비울 수 없습니다"),
                    "VC-GATE-002")
            before = gates.get(key)
            gates[key] = bool(val.get("value"))
            changed[key] = gates[key]
            log.append({"gate": key, "from": before, "to": gates[key],
                        "actor": actor, "evidence": evidence,
                        "note": val.get("note"), "at": _now_iso()})
        p.save()
        return {"gates": gates, "changed": changed, "log_entries": len(log)}
    except Exception as e:
        return err(e)


@router.post("/project/{pid}/cost")
def post_cost(pid: str):
    try:
        return Project(pid).estimate()
    except FileNotFoundError as e:
        return err(e, "VC-GEO-001", 404)
    except Exception as e:
        return err(e)


@router.get("/project/{pid}/cost")
def get_cost(pid: str):
    try:
        p = Project(pid)
    except ValueError as e:
        return err(e, "VC-PROJ-001")
    f = p.dir / "cost.json"
    if not f.exists():
        raise HTTPException(404, "아직 계산되지 않았습니다")
    d = json.loads(f.read_text(encoding="utf-8"))
    # 입력이 바뀌었으면 낡은 결과라고 함께 알려준다. 파일은 건드리지 않는다.
    d["stale"] = p.staleness(d.get("inputs_fingerprint"))
    return d
