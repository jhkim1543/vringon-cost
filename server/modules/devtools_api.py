# -*- coding: utf-8 -*-
"""개발용 라우터 — 뷰어 캡처. 운영(PORT 존재)에서는 닫힌다."""
import base64
import os
import re

from fastapi import APIRouter, HTTPException

from config import DATA

router = APIRouter(prefix="/api", tags=["devtools"])


@router.post("/debug/capture")
def debug_capture(payload: dict):
    """브라우저가 실제로 그린 프레임을 파일로 받는다.

    뷰어 렌더 문제는 서버 쪽 기하 측정만으로는 판정이 안 된다.
    화면 픽셀을 그대로 받아서 눈으로 확인하기 위한 로컬 전용 통로다.
    """
    if os.environ.get("ALLOW_DEBUG") != "1" and os.environ.get("PORT"):
        # EB 는 PORT 를 준다. 운영에서는 이 통로를 닫는다.
        raise HTTPException(404, "운영에서는 비활성")
    d = payload.get("data_url", "")
    if "," not in d:
        raise HTTPException(400, "data URL 형식이 아님")
    raw = str(payload.get("name", "capture"))
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:64] + ".png"
    out = DATA / "debug" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(d.split(",", 1)[1]))
    return {"saved": str(out), "bytes": out.stat().st_size}
