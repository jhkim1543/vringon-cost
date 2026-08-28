# -*- coding: utf-8 -*-
"""원가 계산 무결성 감사.

cost.json 을 신뢰하지 않고 처음부터 다시 곱해 본다. 검사 항목:
  1 라인 산술    cost = gross_qty x price (P10/P50/P90 각각)
  2 소요량 재현  트레이스의 계수로 gross_qty 를 독립 재계산
  3 합계 무결성  known_subtotal = 라인 합, 버킷 분해 합 = 소계
  4 단가 원본    라인 단가 = 워크북 분기 스냅샷 값 (proxy 는 비율 검증)
  5 게이트 논리  차단 라인은 cost 가 None, PARTIAL 이면 총액 None
  6 질량 재현    완제품 질량을 독립 재계산

    python tools/audit_costing.py [프로젝트ID ...]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import catalog  # noqa: E402

TOL = 1e-6
issues = []


def check(ok, label, detail=""):
    mark = "OK " if ok else "FAIL"
    if not ok:
        issues.append(f"{label}: {detail}")
    print(f"    [{mark}] {label}" + (f"  {detail}" if detail and not ok else ""))
    return ok


def param(spec, key):
    p = catalog.param(spec, key)
    return p["value"]


def recompute_qty(line):
    """소재 스펙 파라미터로 소요량을 독립 재계산한다 (켤레 기준)."""
    spec = line.get("material_spec")
    sp = catalog.material_specs().get(spec) or {}
    form = sp.get("form")
    g = line.get("geometry") or {}
    pf2 = 2.0  # 기하 유래는 한 짝 -> 켤레

    if form == "roll":
        net = g.get("surface_area_m2")
        if not net:
            return None
        return net * param(spec, "pattern_factor") / (
            param(spec, "usable_width_m") * param(spec, "nesting_yield")
            * param(spec, "process_yield")) * pf2
    if form == "sheet":
        net = g.get("surface_area_m2")
        if not net:
            return None
        return net * param(spec, "pattern_factor") / (
            param(spec, "sheet_area_m2") * param(spec, "nesting_yield")
            * param(spec, "process_yield")) * pf2
    if form == "sheet_m2":
        net = g.get("surface_area_m2")
        if not net:
            return None
        return net * param(spec, "pattern_factor") / (
            param(spec, "nesting_yield") * param(spec, "process_yield")) * pf2
    if form == "molded":
        vol = g.get("volume_m3")
        if vol is None:
            return None
        return vol * param(spec, "molded_density_kg_m3") / \
            param(spec, "process_yield") * pf2
    if form == "chemical":
        area = g.get("surface_area_m2")
        if not area:
            return None
        return area * param(spec, "wet_coat_kg_m2") * param(spec, "coats") / \
            param(spec, "transfer_efficiency") * pf2
    if form == "thread":
        L = g.get("length_m")
        if not L:
            return None
        return L * param(spec, "stitch_consumption_factor") * \
            param(spec, "waste_factor") * \
            param(spec, "linear_density_tex") / 1e6 * pf2
    if form == "length":
        L = g.get("length_m")
        if not L:
            return None
        wf = param(spec, "waste_factor") or 1.05
        return L * wf * pf2
    if form == "mass":
        return param(spec, "mass_per_pair_kg")
    if form == "count":
        return float(line.get("qty_per_pair") or 1)
    return None


def audit(pid):
    f = ROOT / "data" / "projects" / pid / "cost.json"
    if not f.exists():
        print(f"  {pid}: cost.json 없음, 건너뜀")
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    lines, ru = d["lines"], d["rollup"]
    q = d["scenario"]["quarter"]
    print(f"\n== {pid} ({len(lines)}라인, {q}) ==")

    # 1 라인 산술
    bad = []
    for l in lines:
        g = l["consumption"].get("gross_qty")
        for k in ("p10", "p50", "p90"):
            price = l["price"].get(k)
            got = l.get(f"cost_{k}")
            if g is None or price is None:
                if got is not None:
                    bad.append(f"{l['line_id']} {k}: 입력 없는데 cost={got}")
            elif got is None:
                if l["status"] == "calculated":
                    bad.append(f"{l['line_id']} {k}: cost 누락")
            elif abs(got - g * price) > TOL * max(1, abs(got)):
                bad.append(f"{l['line_id']} {k}: {got} != {g}x{price}")
    check(not bad, "라인 산술 (cost = qty x price)", "; ".join(bad[:3]))

    # 2 소요량 독립 재계산
    bad = []
    for l in lines:
        want = l["consumption"].get("gross_qty")
        if want is None:
            continue
        got = recompute_qty(l)
        if got is None:
            bad.append(f"{l['line_id']}: 재계산 불가(감사식 미지원)")
        elif abs(got - want) > 1e-6 * max(1, abs(want)):
            bad.append(f"{l['line_id']}: 재계산 {got:.6f} != 기록 {want:.6f}")
    check(not bad, "소요량 독립 재계산", "; ".join(bad[:3]))

    # 3 합계 무결성
    # 계약: 확인된 소계 = 승인된 라인의 합. 미승인분은 따로 잡고, 둘을
    # 더하면 전체 라인 합이 되어야 한다 (금액이 사라지지 않았다는 검사).
    appr = [l for l in lines if l.get("approved", True)]
    unappr = [l for l in lines if not l.get("approved", True)]
    for k in ("p10", "p50", "p90"):
        want = ru["known_cost_subtotal"][k]
        got = sum(l.get(f"cost_{k}") or 0.0 for l in appr)
        check(abs(got - want) < 1e-6, f"소계 {k} = 승인 라인 합",
              f"{got:.6f} != {want:.6f}")
    un = ru.get("unapproved_material_subtotal") or {}
    if un:
        for k in ("p10", "p50", "p90"):
            got = sum(l.get(f"cost_{k}") or 0.0 for l in unappr)
            check(abs(got - (un.get(k) or 0.0)) < 1e-6,
                  f"미승인 소계 {k} = 미승인 라인 합", f"{got:.6f}")
        total = sum(l.get("cost_p50") or 0.0 for l in lines)
        check(abs((ru["known_cost_subtotal"]["p50"] + (un.get("p50") or 0.0))
                  - total) < 1e-6, "승인 + 미승인 = 전체 라인 합",
              f"{total:.6f}")
    bd = ru.get("material_breakdown") or {}
    got = sum(v["p50"] for v in bd.values())
    check(abs(got - ru["known_cost_subtotal"]["p50"]) < 1e-6,
          "버킷 분해 합 = 소계", f"{got:.6f}")

    # 4 단가 원본 대조
    snap = catalog.quarterly_prices()
    bad = []
    for l in lines:
        spec, p = l.get("material_spec"), l["price"]
        if not spec or p.get("p50") is None:
            continue
        row = snap.get((q, spec))
        # 단가 단위가 수량 단위와 다르면 엔진이 환산해 저장한다
        # (예: USD/sq ft -> USD/m²). 원본과 비교하려면 되돌려야 한다.
        f = p.get("uom_conversion_factor") or 1.0
        if f and f != 1.0:
            p = {**p, "p10": (p["p10"] or 0) / f, "p50": (p["p50"] or 0) / f,
                 "p90": (p["p90"] or 0) / f}
        if p["basis"] == "quarterly_snapshot":
            if row is None:
                bad.append(f"{spec}: 스냅샷 원본 없음")
            elif any(abs((p[k] or 0) - (row[k] or 0)) > TOL
                     for k in ("p10", "p50", "p90")):
                bad.append(f"{spec}: 값 불일치")
        elif p["basis"] == "price_proxy":
            proxy = (catalog.material_specs().get(spec) or {}).get("price_proxy")
            base = snap.get((q, proxy["spec"])) if proxy else None
            if not (proxy and base and
                    abs(p["p50"] - base["p50"] * proxy["ratio"]) < TOL):
                bad.append(f"{spec}: proxy 비율 불일치")
    check(not bad, "단가 = 워크북 스냅샷 원본", "; ".join(bad[:3]))

    # 5 게이트 논리
    check(all((l.get("cost_p50") is None) == (l["status"] == "blocked")
              for l in lines), "차단 라인은 cost 없음")
    if ru["cost_status"] == "PARTIAL":
        check(ru["fob"] is None and ru["manufacturing_should_cost"] is None,
              "PARTIAL 이면 총액·FOB 없음")
        check("provisional_total" not in ru, "총액 필드 자체가 없음")
    caps = [l for l in lines if (l.get("max_class") or "C2") != "C2"]
    check(d["grade"]["class"] in ("C0", "C1") if caps else True,
          "C1 상한 라인이 있으면 등급도 C1 이하")

    # 6 질량 독립 재계산 (완제품)
    mb = d.get("mass_balance") or {}
    got = 0.0
    for l in lines:
        c, g = l["consumption"], l.get("geometry") or {}
        sp = catalog.material_specs().get(l.get("material_spec")) or {}
        form, asm = sp.get("form"), l.get("assembly") or ""
        if c.get("gross_qty") is None or asm == "Packaging":
            continue
        if form == "chemical":
            got += c["gross_qty"] * 1000 * 0.5
        elif form == "molded" and c.get("net"):
            dv = sp.get("molded_density_kg_m3")
            dv = dv["value"] if isinstance(dv, dict) else dv
            got += c["net"] * dv * 1000 * 2
        elif c.get("uom") == "kg":
            got += c["gross_qty"] * 1000
        else:
            gsm = sp.get("areal_density_gsm")
            gv = gsm["value"] if isinstance(gsm, dict) else gsm
            # 규칙이 파트 두께를 지정했으면 면밀도를 그 비율로 맞춘다
            thk = l.get("thickness_mm_override")
            bt = sp.get("thickness_mm")
            bt = bt["value"] if isinstance(bt, dict) else bt
            if gv and thk and bt and bt > 0:
                gv = gv * thk / bt
            if gv and g.get("surface_area_m2"):
                got += g["surface_area_m2"] * gv * 2
    check(abs(got - mb.get("finished_pair_mass_g", -1)) < 0.5,
          "완제품 질량 재계산",
          f"{got:.1f} != {mb.get('finished_pair_mass_g')}")

    # 요약
    print(f"  소재 P50 ${ru['known_cost_subtotal']['p50']:.3f} | "
          f"{ru['coverage']['priced_lines']}/{ru['coverage']['bom_lines']}라인 | "
          f"완제품 {mb.get('finished_pair_mass_g', 0):.0f}g -> {mb.get('verdict')}")


if __name__ == "__main__":
    pids = sys.argv[1:] or sorted(
        p.name for p in (ROOT / "data" / "projects").iterdir()
        if (p / "cost.json").exists())
    for pid in pids:
        audit(pid)
    print(f"\n총 문제: {len(issues)}건")
    for i in issues:
        print("  -", i)
