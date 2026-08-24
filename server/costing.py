# -*- coding: utf-8 -*-
"""결정론적 Cost Roll-up (계획서 §11, §13).

AI가 원가를 직접 뱉지 않는다. 수량 × 단가, 공정시간 × 공장 Rate,
Tooling 상각, 수율·불량·간접비를 순서대로 더한다.

입력이 없는 항목은 0 으로 숨기지 않고 Blocked 로 남긴다. 그래야
"노무비 0" 이 "노무비 없음"으로 오해되지 않는다 (계획서 §12, §20).
"""
import catalog
import consumption
import pricing
import units

_CLASS_ORDER = {"C0": 0, "C1": 1, "C2": 2, "C3": 3, "C4": 4}


def _min_class(*classes):
    """여러 상한 중 가장 낮은 등급. None 은 무시한다."""
    vals = [c for c in classes if c in _CLASS_ORDER]
    return min(vals, key=lambda c: _CLASS_ORDER[c]) if vals else None



def _mul(qty, price):
    return None if (qty is None or price is None) else qty * price


# 성형 파트의 한 짝 기준 타당 부피 범위 (cm3). 260~300mm 러닝화 문헌·실측 통상치.
# CV(해상도 안정성)와 폭 비율, 총질량 게이트가 다 통과해도 파트 하나가
# 3배 부풀 수 있다는 것을 QA 에서 확인했다. 파트 단위로 자릿수를 잡는다.
PART_VOLUME_RANGE_CM3 = {
    "Midsole Carrier": (100, 350),
    "Outsole Rubber": (30, 130),
    "Midsole Insert": (3, 80),
    "Rubber Pod": (3, 60),
    "TPU Cage/Heel Clip": (3, 60),
}


def volume_warnings(line):
    """성형 라인의 부피가 통상 범위를 벗어나면 경고를 만든다.

    차단이 아니라 경고다. 복원 3D 의 한계로 생기는 과대·과소를 사용자와
    등급 게이트가 볼 수 있게 남기는 것이 목적이다.
    """
    cp = line.get("canonical_part")
    rng = PART_VOLUME_RANGE_CM3.get(cp)
    net = (line.get("consumption") or {}).get("net")
    sp = catalog.material_specs().get(line.get("material_spec")) or {}
    if not rng or not net or sp.get("form") != "molded":
        return []
    vol = float(net) * 1e6
    lo, hi = rng
    if vol > hi:
        return [f"{cp} 부피 {vol:.0f} cm3/짝가 통상 상한 {hi} 을 {vol / hi:.1f}배 초과. "
                "복원 3D 과대(내부 공간 포함) 의심, 승인 sole CAD 필요"]
    if vol < lo:
        return [f"{cp} 부피 {vol:.0f} cm3/짝가 통상 하한 {lo} 미만. 과소 복원 의심"]
    return []


def cost_lines(bom, quarter, supplier_quotes=None):
    """BOM -> 원가 라인. 각 라인에 근거와 차단 사유를 붙인다."""
    out = []
    for line in bom:
        cons = consumption.compute(line)
        spec = line.get("material_spec")
        price = pricing.select(spec, quarter, supplier_quotes) if spec else {
            "basis": "missing", "p10": None, "p50": None, "p90": None,
            "eligibility": "None", "max_class": None, "confidence": "F",
            "stale": True, "note": "소재 미배정",
        }

        blocked = list(cons.get("blocked") or [])
        blocked += list(line.get("qa_blocked") or [])
        if price.get("p50") is None:
            blocked.append(f"단가 없음: {spec or '(소재 미배정)'}")
        if price.get("stale"):
            blocked.append(f"단가 stale ({price.get('note')})")

        ok_uom, uom_note = (True, None)
        if cons.get("gross_qty") is not None and price.get("p50") is not None:
            # 차원 검사. 모르는 단위는 통과시키지 않는다.
            ok_uom, uom_note = units.check_multiply(cons.get("uom"), price.get("uom"))
            if not ok_uom:
                blocked.append(uom_note)
            else:
                sp = catalog.material_specs().get(spec) or {}
                ok_f, f_note = units.check_formula(sp.get("form"), cons.get("uom"))
                if not ok_f:
                    ok_uom = False
                    blocked.append(f_note)

        qty = cons.get("gross_qty") if ok_uom else None
        c10, c50, c90 = (_mul(qty, price.get("p10")),
                         _mul(qty, price.get("p50")),
                         _mul(qty, price.get("p90")))

        out.append({
            **{k: line[k] for k in ("line_id", "part_id", "canonical_part",
                                    "assembly", "visibility", "origin")},
            "material_spec": spec,
            "segments": line.get("segments", []),
            "geometry_role": line.get("geometry_role"),
            "geometry_max_class": line.get("max_class"),
            "price_max_class": price.get("max_class"),
            "quantity_basis": line.get("quantity_basis"),
            "formula_family": line.get("formula_family"),
            # 규칙 근거는 UI 근거 패널의 핵심이다. 여기서 빠뜨리면
            # "왜 이 파트가 BOM 에 있는가"를 화면에서 설명할 수 없다.
            "rule_id": line.get("rule_id"),
            "rule_condition": line.get("rule_condition"),
            "rule_parameters": line.get("rule_parameters"),
            "rule_evidence": line.get("rule_evidence"),
            "approval_role": line.get("approval_role"),
            "factory_inputs_required": line.get("factory_inputs_required"),
            "approval_status": line.get("approval_status"),
            "geometry": line.get("geometry"),
            "consumption": cons,
            "price": price,
            "cost_p10": c10, "cost_p50": c50, "cost_p90": c90,
            "blocked": sorted(set(b for b in blocked if b)),
            "warnings": volume_warnings(line if "consumption" in line else
                                        {**line, "consumption": cons}),
            "status": "calculated" if c50 is not None else "blocked",
            "max_class": _min_class(line.get("max_class"), price.get("max_class")),
            "assumptions": cons.get("assumptions", []),
        })
    return out


def labor_machine(order_qty):
    """15_공정인건비 에서 노무·기계비. 값이 TBD 면 Blocked."""
    ops, labor, machine, blocked = [], 0.0, 0.0, []
    for op in catalog.routing():
        sam = op["sam_min"] or 0.0
        eff = op["line_efficiency"] or 0.0
        rate = op["labor_rate_usd_hr"] or 0.0
        mmin = op["machine_min"] or 0.0
        mrate = op["machine_rate_usd_hr"] or 0.0

        if eff <= 0 or rate <= 0 or sam <= 0:
            blocked.append(f"{op['op_id']} {op['operation']}: SAM/효율/rate 미입력")
            lc = None
        else:
            setup = (op["setup_min"] or 0.0) / max(op["batch_qty"] or 1, 1)
            lc = (sam / eff) * rate / 60.0 + setup * rate / 60.0
            labor += lc
        mc = (mmin * mrate / 60.0) if (mmin > 0 and mrate > 0) else None
        if mc:
            machine += mc
        ops.append({**op, "labor_cost_pair": lc, "machine_cost_pair": mc})

    complete = not blocked
    return {
        "operations": ops,
        "labor_usd_pair": labor if complete else None,
        "machine_usd_pair": machine if complete else None,
        "blocked": blocked,
        "status": "calculated" if complete else "blocked",
    }


def tooling(order_qty):
    """17_Tooling마스터 에서 켤레당 상각. 견적이 0 이면 Blocked."""
    rows, total, blocked = [], 0.0, []
    for t in catalog.tooling():
        cost = t["tool_cost_usd"] or 0.0
        life = t["tool_life_pairs"] or 0.0
        alloc = t["allocation_qty"] or order_qty or 0.0
        if cost <= 0 or (life <= 0 and alloc <= 0):
            blocked.append(f"{t['tool_id']} {t['tool_type']}: 금형 견적 미입력")
            rows.append({**t, "cost_per_pair": None})
            continue
        base = min(x for x in (life, alloc) if x > 0)
        per = cost * (1 + (t["repair_allowance_pct"] or 0.0) / 100.0) / base
        total += per
        rows.append({**t, "cost_per_pair": per})
    complete = not blocked
    return {
        "tools": rows,
        "usd_pair": total if complete else None,
        "blocked": blocked,
        "status": "calculated" if complete else "blocked",
    }


PACKAGING_ASSEMBLIES = {"Packaging"}
CHEMICAL_ASSEMBLIES = {"Chemical"}


def mass_balance(lines, target_pair_g=None):
    """완제품 질량과 구매 투입 질량을 분리해 검증한다 (외부 검토 반영).

    이전 구현의 오류 두 가지를 고친다.
    1. 투입 질량(수율 나눔 포함)을 완제품 질량 검증에 썼다. 완제품에는
       순질량(부피 x 밀도)을 써야 한다. 수율 손실분은 신발에 남지 않는다.
    2. 포장재(박스·티슈)와 접착제 습량을 완제품 질량에 섞었다. 목표 무게가
       신발 켤레 무게라면 포장은 제외하고, 접착제는 건조 고형분만 남는다.
    """
    finished_g, purchased_g = 0.0, 0.0
    by_line, unknown, excluded = [], [], []

    for l in lines:
        c = l.get("consumption") or {}
        q, uom = c.get("gross_qty"), c.get("uom")
        spec = l.get("material_spec")
        sp = catalog.material_specs().get(spec) or {}
        form = sp.get("form")
        assembly = l.get("assembly") or ""
        part = l["canonical_part"]

        if q is None:
            unknown.append(part)
            continue

        # 구매 투입 질량 (kg 라인은 그대로)
        if uom == "kg":
            purchased_g += q * 1000.0

        # 완제품 질량
        if assembly in PACKAGING_ASSEMBLIES:
            excluded.append({"canonical_part": part, "reason": "포장재"})
            continue
        if form == "chemical" or assembly in CHEMICAL_ASSEMBLIES:
            # 습도포량이 아니라 건조 고형분만 남는다. 고형분비는 공장값이라
            # 보수적으로 50% 를 쓰고 근거를 남긴다.
            if uom == "kg":
                g = q * 1000.0 * 0.5
                finished_g += g
                by_line.append({"canonical_part": part, "grams": g,
                                "via": "chemical_dry_solids_50pct"})
            continue
        if form == "molded":
            # 완제품에는 순질량. consumption.net 은 한 짝 부피(m3)다.
            net_vol = c.get("net")
            dens = sp.get("molded_density_kg_m3")
            dv = dens["value"] if isinstance(dens, dict) else dens
            if net_vol and dv:
                g = float(net_vol) * float(dv) * 1000.0 * 2.0
                finished_g += g
                by_line.append({"canonical_part": part, "grams": g,
                                "via": "net_volume_x_density"})
            else:
                unknown.append(part)
            continue
        if uom == "kg":
            finished_g += q * 1000.0
            by_line.append({"canonical_part": part, "grams": q * 1000.0,
                            "via": "kg"})
            continue
        # 면적 소재: 순면적 x 면밀도(GSM) x 2짝
        gsm = sp.get("areal_density_gsm")
        gv = gsm["value"] if isinstance(gsm, dict) else gsm
        net = (l.get("geometry") or {}).get("surface_area_m2")
        if gv and net:
            g = float(net) * float(gv) * 2.0
            finished_g += g
            by_line.append({"canonical_part": part, "grams": g, "via": "gsm"})
        else:
            unknown.append(part)

    out = {
        "finished_pair_mass_g": finished_g,
        "purchased_input_mass_g": purchased_g,
        "known_mass_g": finished_g,          # 하위 호환
        "lines_counted": len(by_line),
        "lines_without_mass": sorted(set(unknown)),
        "excluded_packaging": excluded,
        "top": sorted(by_line, key=lambda d: -d["grams"])[:6],
        "target_pair_g": target_pair_g,
        "coverage": None,
        "verdict": "no_target",
        "note": ("완제품 질량은 순질량 기준이다. 부피 파트는 부피 x 밀도, "
                 "면적 파트는 순면적 x 면밀도, 접착제는 건조 고형분 50%, "
                 "포장재는 제외."),
    }
    if target_pair_g:
        cov = finished_g / float(target_pair_g)
        out["coverage"] = cov
        many_uncounted = len(out["lines_without_mass"]) >= 5
        if cov > 1.05 or (cov > 0.85 and many_uncounted):
            out["verdict"] = "fail_over_estimate"
            out["note"] += (f" 질량 미산정 {len(out['lines_without_mass'])}건이 "
                            f"남았는데 이미 목표의 {cov:.0%}다. 부피 과대추정.")
        elif cov >= 0.55:
            out["verdict"] = "ok"
        elif cov >= 0.3:
            out["verdict"] = "needs_review"
        else:
            out["verdict"] = "suspect_missing_bom"
    return out


def bucket_breakdown(lines):
    """소재 소계를 조립군별로 쪼갠다 (외부 검토: 포장이 소재비의 31% 를
    차지하는데 한 덩어리로 보이면 소재비가 왜곡돼 보인다)."""
    groups = {}
    for l in lines:
        if l.get("cost_p50") is None:
            continue
        a = l.get("assembly") or "기타"
        key = ("Packaging" if a in PACKAGING_ASSEMBLIES else
               "Chemical" if a in CHEMICAL_ASSEMBLIES else
               "Sole" if a == "Bottom" else
               "Upper" if a.startswith("Upper") or a in ("Padding", "Reinforcement", "Waterproof") else
               "Trim" if a == "Trim" else "기타")
        g = groups.setdefault(key, {"p10": 0.0, "p50": 0.0, "p90": 0.0, "lines": 0})
        for k in ("p10", "p50", "p90"):
            g[k] += l.get(f"cost_{k}") or 0.0
        g["lines"] += 1
    return groups


# 전체 제조원가를 내려면 반드시 채워져야 하는 버킷.
REQUIRED_BUCKETS = ("Material", "Direct Labor", "Machine", "Tooling Amortization")


def roll_up(lines, scenario):
    """소재, 노무, 기계, 금형을 합산한다.

    부분 원가를 총원가처럼 내보내지 않는다. 노무, 기계, 금형 중 하나라도
    막혀 있으면 전체 제조원가와 FOB 는 None 이다. 소재비만 아는 상태에서
    간접비와 마진을 곱하면 그 비율의 기준이 전체 제조비인데 분모가
    소재비뿐이라 숫자가 조용히 왜곡된다.
    """
    order_qty = scenario.get("order_quantity") or 0
    mat = {k: sum(l[f"cost_{k}"] or 0.0 for l in lines) for k in ("p10", "p50", "p90")}
    mat_blocked = [l["line_id"] for l in lines if l["status"] == "blocked"]

    lm = labor_machine(order_qty)
    tl = tooling(order_qty)

    def bucket(name, p10, p50, p90, status, coverage, note=None):
        return {"bucket": name, "p10": p10, "p50": p50, "p90": p90,
                "status": status, "coverage": coverage, "note": note}

    buckets = [
        bucket("Material", mat["p10"], mat["p50"], mat["p90"],
               "Calculated" if not mat_blocked else "Partial",
               "Quarterly snapshot",
               f"{len(mat_blocked)}개 라인 차단" if mat_blocked else None),
        bucket("Direct Labor", *( (lm["labor_usd_pair"],)*3 if lm["labor_usd_pair"] is not None
                                  else (None, None, None)),
               status=lm["status"].capitalize(), coverage="Factory data required",
               note="; ".join(lm["blocked"][:2]) if lm["blocked"] else None),
        bucket("Machine", *((lm["machine_usd_pair"],)*3 if lm["machine_usd_pair"] is not None
                            else (None, None, None)),
               status=lm["status"].capitalize(), coverage="Factory data required"),
        bucket("Tooling Amortization", *((tl["usd_pair"],)*3 if tl["usd_pair"] is not None
                                         else (None, None, None)),
               status=tl["status"].capitalize(), coverage="Tool quote required",
               note="; ".join(tl["blocked"][:2]) if tl["blocked"] else None),
    ]

    blocked_buckets = [b["bucket"] for b in buckets
                       if b["bucket"] in REQUIRED_BUCKETS and b["p50"] is None]
    complete = not blocked_buckets

    # 아는 것만 더한 소계. 총원가가 아니라는 이름을 쓴다.
    known = {k: sum(b[k] for b in buckets if b[k] is not None)
             for k in ("p10", "p50", "p90")}

    rej = scenario.get("reject_allowance_pct", 3.0) / 100.0
    ovh = scenario.get("factory_overhead_pct", 8.0) / 100.0
    mar = scenario.get("supplier_margin_pct", 10.0) / 100.0

    if complete:
        reject = {k: known[k] * rej for k in known}
        overhead = {k: known[k] * ovh for k in known}
        mfg = {k: known[k] + reject[k] + overhead[k] for k in known}
        margin = {k: mfg[k] * mar for k in mfg}
        fob = {k: mfg[k] + margin[k] for k in mfg}
        status = "COMPLETE"
    else:
        # 부분 상태에서는 비율 항목을 아예 계산하지 않는다.
        reject = overhead = mfg = margin = fob = None
        status = "PARTIAL"

    priced = [l for l in lines if l["status"] == "calculated"]
    sanity = [w for l in lines for w in (l.get("warnings") or [])]
    return {
        "cost_status": status,
        "sanity_warnings": sanity,
        "material_breakdown": bucket_breakdown(lines),
        "buckets": buckets,
        "known_cost_subtotal": known,
        "blocked_buckets": blocked_buckets,
        "reject_allowance": reject,
        "factory_overhead": overhead,
        "manufacturing_should_cost": mfg,
        "supplier_margin": margin,
        "fob": fob,
        "direct_complete": complete,
        "fob_status": "Calculated" if complete else "산출 불가",
        "coverage": {
            "bom_lines": len(lines),
            "priced_lines": len(priced),
            "unpriced_lines": len(mat_blocked),
            "priced_ratio": (len(priced) / len(lines)) if lines else 0.0,
        },
        "blocked": lm["blocked"] + tl["blocked"] + [f"BOM {i}" for i in mat_blocked],
        "labor_machine": lm,
        "tooling": tl,
        "material_blocked_lines": mat_blocked,
    }


def grade(lines, rollup, gates):
    """C0–C4 등급 판정 (계획서 §2, §12).

    gates: {gate_key: bool} — 사용자가 확정한 것만 True.
    """
    from config import CLASS_REQUIREMENTS

    reasons = {"C1": [], "C2": []}
    for cls in ("C1", "C2"):
        for key, label in CLASS_REQUIREMENTS[cls]:
            if not gates.get(key):
                reasons[cls].append(label)

    # 단가 자격이 Engineering 이 아니면 C2 불가
    non_eng = sorted({l["material_spec"] for l in lines
                      if l.get("price", {}).get("eligibility") not in ("Engineering",)
                      and l.get("material_spec")})
    if non_eng:
        reasons["C2"].append(
            f"승인 Supplier 견적이 아닌 단가 {len(non_eng)}건 (분기 스냅샷/공개 리스팅)")
    if not rollup["direct_complete"]:
        reasons["C2"].append("공장 routing, SAM, tooling 미확정")

    # 복구 부피나 표면 proxy 로 잡힌 라인은 C2 로 올라갈 수 없다.
    capped = sorted({l["canonical_part"] for l in lines
                     if (l.get("max_class") or "C2") != "C2"})
    if capped:
        reasons["C2"].append(
            f"C1 상한 지오메트리 {len(capped)}건 (복구 부피 또는 표면 proxy): "
            + ", ".join(capped[:4]) + ("…" if len(capped) > 4 else ""))

    if not reasons["C1"]:
        cls = "C2" if not reasons["C2"] else "C1"
    else:
        cls = "C0"
    return {"class": cls, "blocked_reasons": reasons,
            "downgraded_from": "C2" if cls != "C2" else None}
