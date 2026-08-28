# -*- coding: utf-8 -*-
"""가격 선택과 분기 스냅샷 (계획서 §12).

두 가지를 절대 하지 않는다.
  1. 공개 리스팅 가격을 조용히 C2(Engineering) 단가로 승격하지 않는다.
  2. 새 분기에 데이터가 없다고 이전 가격을 일반 가격처럼 복사하지 않는다.
     stale=true, confidence=D 로 이관하고 C2 계산을 막는다.
"""
import catalog
from config import PRICE_BASIS_RANK, ELIGIBILITY_MAX_CLASS

# UOM 정규화: 워크북 표기 -> 계산에서 쓰는 표기
UOM_ALIAS = {
    "m": "m", "m²": "m²", "m2": "m²", "sq ft": "sq ft", "sheet": "sheet",
    "kg": "kg", "pair": "pair", "piece": "piece", "ea": "piece",
}



# 가격 신뢰도 계층 (외부 검토 2026-08-28).
#   A4 Actual     송장·입고·실제 결제
#   A3 Committed  승인 PO·유효 계약단가
#   A2 Quoted     유효한 공급사 견적
#   A1 Benchmark  원자재·환율·운임·시장 지수
#   A0 Estimated  AI 추정·웹 판매가·공개 카탈로그 리스팅
# 현재 데이터는 전부 A0/A1 이다. 견적이 들어와야 A2 가 생긴다.
def source_tier(basis, price_basis=None):
    if basis == "approved_supplier_quote":
        return "A2", "Quoted"
    if basis in ("quarterly_snapshot", "carried_forward", "price_proxy"):
        pb = (price_basis or "").lower()
        if "지수" in (price_basis or "") or "index" in pb:
            return "A1", "Benchmark"
        return "A0", "Estimated"
    return None, None


def select(spec_id, quarter, supplier_quotes=None):
    out = _select(spec_id, quarter, supplier_quotes)
    tier, label = source_tier(out.get("basis"), out.get("price_basis"))
    out.setdefault("source_tier", tier)
    out.setdefault("tier_label", label)
    return out


def _select(spec_id, quarter, supplier_quotes=None):
    """한 소재의 단가를 우선순위에 따라 고른다.

    승인 Supplier Quote > 과거승인+지수 에스컬레이션 > 유사 승인견적
    > 분기 스냅샷 > 공개 리스팅   (계획서 §12.2)
    """
    supplier_quotes = supplier_quotes or {}

    q = supplier_quotes.get(spec_id)
    if q and q.get("approved"):
        return {
            "spec_id": spec_id,
            "p10": q["p10"], "p50": q["p50"], "p90": q["p90"],
            "currency": q.get("currency", "USD"),
            "uom": UOM_ALIAS.get(q.get("uom"), q.get("uom")),
            "basis": "approved_supplier_quote",
            "basis_rank": PRICE_BASIS_RANK["approved_supplier_quote"],
            "eligibility": "Engineering",
            "max_class": "C2",
            "confidence": "A",
            "stale": False,
            "source_url": q.get("source_url"),
            "note": "승인 공급사 견적",
        }

    snap = catalog.quarterly_prices().get((quarter, spec_id))

    # 워크북에 자체 단가가 없는 소재(cleaner, primer)는 관련 소재 단가에
    # 비율을 곱해 쓴다. 반드시 proxy 로 표시하고 신뢰도를 낮춘다.
    if snap is None:
        proxy = (catalog.material_specs().get(spec_id) or {}).get("price_proxy")
        if proxy:
            base = select(proxy["spec"], quarter, supplier_quotes)
            if base.get("p50") is not None:
                r = float(proxy["ratio"])
                return {
                    **base, "spec_id": spec_id,
                    "p10": base["p10"] * r, "p50": base["p50"] * r,
                    "p90": base["p90"] * r,
                    "basis": "price_proxy",
                    "eligibility": "Concept only", "max_class": "C1",
                    "confidence": "D", "stale": False,
                    "note": f"{proxy['spec']} 단가 × {r} (자체 단가 없음, {proxy.get('note','')})".strip(),
                }

    if snap is None:
        # 이전 분기 값을 이관하되 stale 로 낙인찍는다.
        prev = _latest_before(spec_id, quarter)
        if prev is None:
            return {
                "spec_id": spec_id, "p10": None, "p50": None, "p90": None,
                "basis": "missing", "eligibility": "None", "max_class": None,
                "confidence": "F", "stale": True,
                "note": f"{quarter} 및 이전 분기에 단가 없음 -> RFQ 필요",
            }
        return {
            **_from_snapshot(prev),
            "basis": "carried_forward",
            "eligibility": "Concept only",
            "max_class": "C1",
            "confidence": "D",
            "stale": True,
            "note": f"{quarter} 신규 데이터 없음 -> {prev['quarter']} 값 이관 (C2 차단)",
        }

    return _from_snapshot(snap)


def _from_snapshot(s):
    elig = s.get("eligibility") or "Concept only"
    return {
        "spec_id": s["spec_id"],
        "p10": s.get("p10"), "p50": s.get("p50"), "p90": s.get("p90"),
        "currency": s.get("currency", "USD"),
        "uom": UOM_ALIAS.get(s.get("uom"), s.get("uom")),
        "basis": "quarterly_snapshot",
        "basis_rank": PRICE_BASIS_RANK["quarterly_snapshot"],
        "eligibility": elig,
        "max_class": ELIGIBILITY_MAX_CLASS.get(elig, "C1"),
        "confidence": s.get("confidence"),
        "stale": bool(s.get("stale")),
        "quarter": s.get("quarter"),
        "region": s.get("region"),
        "price_basis": s.get("price_basis"),
        "source_url": s.get("source_url"),
        "note": s.get("note"),
    }


def _latest_before(spec_id, quarter):
    cands = [v for (q, sid), v in catalog.quarterly_prices().items()
             if sid == spec_id and q < quarter]
    return max(cands, key=lambda v: v["quarter"]) if cands else None


def uom_match(price_uom, qty_uom):
    """단가 UOM 과 소요량 UOM 이 맞는지 본다.

    안 맞으면 조용히 곱하지 않는다. m 단가를 m² 수량에 곱하려면
    유효 폭이 필요하고, 그건 소재 스펙에서 이미 반영했어야 한다.
    """
    if price_uom is None or qty_uom is None:
        return False, "UOM 미상"
    a, b = UOM_ALIAS.get(price_uom, price_uom), UOM_ALIAS.get(qty_uom, qty_uom)
    if a == b:
        return True, None
    if {a, b} == {"piece", "pair"}:
        return True, "piece/pair 동일 취급"
    return False, f"단가 UOM '{a}' 와 소요량 UOM '{b}' 불일치"


def make_snapshot(quarter, observations=None):
    """분기 스냅샷 생성 (계획서 §12.3의 축약 구현).

    신규 관측이 없으면 이전 분기를 stale 로 이관한다. 이 동작이 핵심이다.
    """
    prices = catalog.quarterly_prices()
    specs = sorted({sid for (_q, sid) in prices})
    out, fresh, stale = [], 0, 0
    for sid in specs:
        obs = (observations or {}).get(sid)
        if obs:
            out.append({"quarter": quarter, "spec_id": sid, **obs,
                        "stale": False, "confidence": obs.get("confidence", "C")})
            fresh += 1
        else:
            prev = _latest_before(sid, quarter) or prices.get((max(
                q for (q, s) in prices if s == sid), sid))
            if prev:
                out.append({**prev, "quarter": quarter, "stale": True,
                            "confidence": "D", "eligibility": "Concept only",
                            "note": f"{quarter} 신규 데이터 없음 -> 이관"})
                stale += 1
    return {"quarter": quarter, "rows": out, "fresh": fresh, "stale": stale}
