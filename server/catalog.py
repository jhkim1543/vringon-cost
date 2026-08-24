# -*- coding: utf-8 -*-
"""워크북 시드 + 소재 스펙 카탈로그 로더.

시드 JSON은 워크북 원본을 그대로 담고 있으므로 한글 컬럼명이 그대로다.
여기서 한 번만 정규화해서 나머지 모듈이 영문 키만 보게 한다.
"""
import json
from functools import lru_cache

from config import SEED, DATA


def _load(name):
    p = SEED / name
    if not p.exists():
        raise FileNotFoundError(f"시드가 없습니다: {p}\n먼저 tools/seed_from_xlsx.py 를 실행하세요.")
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def bom_master():
    """Canonical BOM 마스터 -> {canonical_part: {...}}"""
    out = {}
    for r in _load("bom_master.json"):
        out[r["Canonical Part"]] = {
            "part_id": r["Part_ID"],
            "assembly": r["Assembly"],
            "canonical_part": r["Canonical Part"],
            "visibility": r["Visible/Hidden"],
            "segmentation_expected": r.get("Segmentation Expected") == "Yes",
            "qty_basis": r.get("Default Qty Basis"),
            "geometry_metric": r.get("Geometry Metric"),
            "uom": r.get("Default UOM"),
            "formula_family": r.get("Cost Formula Family"),
            "construction_dependency": r.get("Construction Dependency"),
            "priority": r.get("MVP Priority"),
            "description": r.get("설명"),
        }
    return out


@lru_cache(maxsize=1)
def recipes():
    """Construction rule -> 리스트 (우선순위 순)."""
    out = []
    for r in _load("construction_recipes.json"):
        out.append({
            "rule_id": r["Rule_ID"],
            "construction": r["Construction"],
            "condition": r["Condition Expression"],
            "add_part": r["Add BOM Part"],
            "material_role": r.get("Material Role"),
            "qty_method": r.get("Qty Method"),
            "parameters": _parse_params(r.get("Default Parameter")),
            "raw_parameters": r.get("Default Parameter"),
            "required": r.get("Required") == "Yes",
            "priority": r.get("Priority"),
            "evidence": r.get("Evidence Source"),
            "approval_role": r.get("Approval"),
        })
    return out


def _parse_params(text):
    """'allowance=1.05; yield=0.85' -> {'allowance':1.05,'yield':0.85}

    'coat_kg_m2=factory' 처럼 숫자가 아닌 값은 문자열로 남겨 공장 입력 필요를 표시한다.
    """
    out = {}
    if not text:
        return out
    for chunk in str(text).split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        k, v = k.strip(), v.strip()
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def quarterly_prices():
    """분기 기준단가 -> {(quarter, spec_id): {...}}"""
    out = {}
    for r in _load("quarterly_prices.json"):
        out[(r["Quarter"], r["MaterialSpec_ID"])] = {
            "quarter": r["Quarter"],
            "spec_id": r["MaterialSpec_ID"],
            "part_group": r.get("Part Group"),
            "description": r.get("Material Description"),
            "region": r.get("Region"),
            "p10": r.get("P10"), "p50": r.get("P50"), "p90": r.get("P90"),
            "currency": r.get("Currency"), "uom": r.get("UOM"),
            "price_basis": r.get("Price Basis"),
            "eligibility": r.get("Cost Eligibility"),
            "confidence": r.get("Confidence"),
            "valid_from": r.get("Valid From"), "valid_to": r.get("Valid To"),
            "source_url": r.get("Source URL"),
            "note": r.get("Note"),
            "stale": False,
        }
    return out


@lru_cache(maxsize=1)
def routing():
    """공정·인건비. 워크북 값이 전부 0/TBD 이면 그대로 두고 blocked 로 표시한다."""
    out = []
    for r in _load("routing.json"):
        out.append({
            "op_id": r["Operation_ID"],
            "seq": r.get("Seq"),
            "operation": r.get("Operation"),
            "target": r.get("Assembly/Part"),
            "workcenter": r.get("Machine/Workcenter"),
            "sam_min": _f(r.get("SAM min")),
            "setup_min": _f(r.get("Setup min/batch")),
            "batch_qty": _f(r.get("Batch Qty")),
            "line_efficiency": _f(r.get("Line Efficiency")),
            "labor_rate_usd_hr": _f(r.get("Loaded Labor USD/hr")),
            "machine_min": _f(r.get("Machine min")),
            "machine_rate_usd_hr": _f(r.get("Machine USD/hr")),
            "data_status": r.get("Data Status"),
            "note": r.get("Note"),
        })
    return out


@lru_cache(maxsize=1)
def tooling():
    out = []
    for r in _load("tooling.json"):
        out.append({
            "tool_id": r["Tooling_ID"],
            "tool_type": r.get("Tool Type"),
            "part": r.get("Applicable Part"),
            "cavity": _f(r.get("Cavity")),
            "tool_cost_usd": _f(r.get("Tool Cost USD")),
            "tool_life_pairs": _f(r.get("Tool Life Pairs")),
            "allocation_qty": _f(r.get("Allocation Qty")),
            "repair_allowance_pct": _f(r.get("Repair Allowance %")),
            "status": r.get("Status"),
        })
    return out


@lru_cache(maxsize=1)
def fx():
    """통화·단위 환산. 워크북 06 시트는 두 표가 한 시트에 겹쳐 있어 따로 추린다."""
    rates, units = {}, {}
    for r in _load("fx.json"):
        cur, krw = r.get("통화"), r.get("KRW per currency")
        if cur and isinstance(krw, (int, float)):
            rates[cur] = float(krw)
        u, f = r.get("단위"), r.get("환산계수")
        if u and isinstance(f, (int, float)):
            units[u] = float(f)
    rates.setdefault("USD", 1386.5)
    rates.setdefault("KRW", 1.0)
    return {"krw_per": rates, "unit_factors": units}


@lru_cache(maxsize=1)
def material_specs():
    d = json.loads((DATA / "material_specs.json").read_text(encoding="utf-8"))
    return d["specs"]


@lru_cache(maxsize=1)
def part_defaults():
    d = json.loads((DATA / "material_specs.json").read_text(encoding="utf-8"))
    return d["part_defaults"]


@lru_cache(maxsize=1)
def part_material_map():
    """01_파트소재맵 — UI의 소재 후보 목록 및 근거 URL 표시에 쓴다."""
    out = []
    for r in _load("part_material_map.json"):
        out.append({
            "part_group": r.get("파트그룹"),
            "part": r.get("세부파트"),
            "material": r.get("주요소재"),
            "spec": r.get("대표사양"),
            "uom": r.get("구매UOM"),
            "usd_low": r.get("USD 하한"), "usd_high": r.get("USD 상한"),
            "tier": r.get("가격층"), "confidence": r.get("신뢰"),
            "driver": r.get("핵심 Cost Driver"),
            "source": r.get("업데이트 소스"), "url": r.get("원본 URL"),
        })
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def param(spec_id, key, default=None):
    """소재 파라미터 하나를 (값, 출처, 메모) 로 꺼낸다."""
    sp = material_specs().get(spec_id, {})
    p = sp.get(key)
    if p is None:
        return {"value": default, "source": "missing", "note": f"{spec_id}.{key} 미정의"}
    if isinstance(p, dict) and "value" in p:
        return {"value": p["value"], "source": p.get("source", "assumption"), "note": p.get("note")}
    return {"value": p, "source": "workbook", "note": None}
