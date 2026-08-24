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


def _mul(qty, price):
    return None if (qty is None or price is None) else qty * price


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
            ok_uom, uom_note = pricing.uom_match(price.get("uom"), cons.get("uom"))
            if not ok_uom:
                blocked.append(uom_note)

        qty = cons.get("gross_qty") if ok_uom else None
        c10, c50, c90 = (_mul(qty, price.get("p10")),
                         _mul(qty, price.get("p50")),
                         _mul(qty, price.get("p90")))

        out.append({
            **{k: line[k] for k in ("line_id", "part_id", "canonical_part",
                                    "assembly", "visibility", "origin")},
            "material_spec": spec,
            "segments": line.get("segments", []),
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
            "status": "calculated" if c50 is not None else "blocked",
            "max_class": price.get("max_class"),
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


def roll_up(lines, scenario):
    """소재 + 노무 + 기계 + 금형 -> Should-Cost. 없는 것은 없다고 표시한다."""
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

    # Direct subtotal 은 계산된 것만 더한다. 차단된 항목은 0 으로 치지 않는다.
    def s(k):
        return sum(b[k] for b in buckets if b[k] is not None)

    direct = {k: s(k) for k in ("p10", "p50", "p90")}
    direct_complete = all(b[k] is not None for b in buckets for k in ("p50",))

    rej = scenario.get("reject_allowance_pct", 3.0) / 100.0
    ovh = scenario.get("factory_overhead_pct", 8.0) / 100.0
    mar = scenario.get("supplier_margin_pct", 10.0) / 100.0

    reject = {k: direct[k] * rej for k in direct}
    overhead = {k: direct[k] * ovh for k in direct}
    mfg = {k: direct[k] + reject[k] + overhead[k] for k in direct}
    margin = {k: mfg[k] * mar for k in mfg}
    total = {k: mfg[k] + margin[k] for k in mfg}

    blocked_all = lm["blocked"] + tl["blocked"] + \
        [f"BOM {lid}" for lid in mat_blocked]

    return {
        "buckets": buckets,
        "direct_subtotal": direct,
        "reject_allowance": reject,
        "factory_overhead": overhead,
        "manufacturing_should_cost": mfg,
        "supplier_margin": margin,
        "provisional_total": total,
        "direct_complete": direct_complete,
        # 완전하지 않으면 FOB 로 내보내지 않는다.
        "fob_status": "Calculated" if direct_complete else "Blocked as FOB",
        "blocked": blocked_all,
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
        reasons["C2"].append("공장 routing·SAM·tooling 미확정")

    if not reasons["C1"]:
        cls = "C2" if not reasons["C2"] else "C1"
    else:
        cls = "C0"
    return {"class": cls, "blocked_reasons": reasons,
            "downgraded_from": "C2" if cls != "C2" else None}
