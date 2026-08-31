# -*- coding: utf-8 -*-
"""Pricing 도메인 라우터 — 분기 단가·스냅샷·시장 지수(A0/A1 참고)."""
import json

from fastapi import APIRouter

import catalog
import pricing
from config import DATA
from core.errors import err

router = APIRouter(prefix="/api", tags=["pricing"])


@router.get("/prices/{quarter}")
def get_prices(quarter: str):
    rows = [v for (q, _s), v in catalog.quarterly_prices().items() if q == quarter]
    return {"quarter": quarter, "rows": sorted(rows, key=lambda r: r["spec_id"])}


@router.post("/prices/snapshot")
def post_snapshot(payload: dict):
    """다음 분기 스냅샷 생성. 신규 관측이 없으면 stale 이관되는지 보여준다."""
    try:
        return pricing.make_snapshot(payload.get("quarter", "2026Q4"),
                                     payload.get("observations"))
    except Exception as e:
        return err(e)


@router.get("/benchmarks")
def get_benchmarks():
    """공개 시장 지수(A1)와 웹 조사치(A0). 원가 단가를 바꾸지 않는 참고용.

    tools/fetch_benchmarks.py, tools/research_component_prices.py 가 만든
    파일을 그대로 서빙한다. 없으면 아직 수집 전이라고 말한다.
    """
    out = {"index": None, "research": None,
           "note": "지수와 조사치는 A1/A0 참고 데이터다. 계산 단가는 분기 "
                   "스냅샷에서만 온다."}
    f = DATA / "benchmarks" / "latest.json"
    if f.exists():
        out["index"] = json.loads(f.read_text(encoding="utf-8"))
    f2 = DATA / "benchmarks" / "component_research.json"
    if f2.exists():
        out["research"] = json.loads(f2.read_text(encoding="utf-8"))
    return out
