# -*- coding: utf-8 -*-
"""Manufacturing BOM 생성.

두 갈래로 라인을 만든다.
  1) 보이는 파트: 확정된 세그먼트 매핑에서 (실측 지오메트리)
  2) 숨은 파트  : ConstructionRecipe 규칙에서 (계획서 §9)

규칙은 워크북 14_ConstructionRecipe 를 그대로 쓴다. LLM 문장을 실행하지 않는다.
조건식은 화이트리스트 토큰만 평가한다 — eval 을 쓰지 않는다.
"""
import re

import catalog
from geometry import (VOLUME_ALLOWED_PARTS, ROLE_MAX_CLASS,
                      classify_role, part_metrics)

# 조건식에 쓸 수 있는 플래그. 워크북 R-001..R-021 의 Condition Expression 에서 뽑았다.
CONSTRUCTION_FLAGS = {
    "always": True,
    "upper_contains_mesh": True,
    "has_tongue": True,
    "has_collar": True,
    "closed_toe": True,
    "heel_support_required": True,
    "has_eyelets": True,
    "lightweight_upper": True,
    "has_no_sew_overlay": True,
    "waterproof": False,
    "cemented_sole": True,
    # 신발용 PU 접착제는 통상 2액형이라 하드너가 실재한다. 워크북에 하드너
    # 단가가 없어(RFQ) 라인은 차단으로 뜨지만, 0 으로 숨기는 것보다 낫다.
    "adhesive_requires_hardener": True,
    # 봉제 어퍼면 봉제사가 든다. 완전 무봉제(니트 일체형)면 꺼야 한다.
    "stitched_upper": True,
    # 메시 러닝화는 보통 원단 아일렛이라 금속 하드웨어가 없다.
    "has_metal_eyelets": False,
    "has_laces": True,
    "has_print": False,
    "midsole_is_painted": False,
    "market_requires_polybag": True,
}

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")



# 레시피의 기하 배율. 워크북 "Default Parameter" 의 coverage/ratio/allowance/factor
# 는 "측정한 면적 중 이 파트가 실제로 덮는 비율"이다. 이것을 안 쓰면 토퍼프가
# 앞코 전체를, 안감이 어퍼 전체를 덮는 것으로 계산된다 (과대계상).
#
# pattern/yield 는 여기서 쓰지 않는다. 그 둘은 소재 스펙의 pattern_factor·
# nesting_yield·process_yield 가 이미 들고 있어서 겹쳐 곱하면 이중 적용이 된다.
GEOMETRY_SCALE_KEYS = ("coverage", "ratio", "allowance", "factor")


def apply_rule_scale(meas, rule):
    """레시피 파라미터의 기하 배율을 측정값에 반영한다."""
    if meas.get("method") in ("blocked", "count"):
        return meas
    scale, used = 1.0, []
    for k in GEOMETRY_SCALE_KEYS:
        v = (rule.get("parameters") or {}).get(k)
        if isinstance(v, (int, float)) and v > 0:
            scale *= float(v)
            used.append(f"{k}={v}")
    if not used or abs(scale - 1.0) < 1e-12:
        return meas
    out = dict(meas)
    out["value"] = meas["value"] * scale
    note = f"규칙 {rule['rule_id']} 배율 {' x '.join(used)}"
    out["note"] = f"{meas.get('note')} · {note}" if meas.get("note") else note
    return out


# 소재 스펙이 이미 들고 있어 규칙 쪽 값을 쓰지 않는 키. 겹쳐 곱하면 이중 적용.
SPEC_OWNED_KEYS = ("pattern", "yield", "coats", "transfer")
# 개수 규칙에서 켤레 환산에 쓰는 키.
COUNT_KEYS = ("per_shoe",)


def unhandled_params(rule):
    """규칙에 적혔지만 계산에 반영되지 않는 숫자 파라미터를 찾는다."""
    known = GEOMETRY_SCALE_KEYS + SPEC_OWNED_KEYS + COUNT_KEYS
    return sorted(k for k, v in (rule.get("parameters") or {}).items()
                  if isinstance(v, (int, float)) and k not in known)


def eval_condition(expr, flags):
    """'has_eyelets && lightweight_upper', 'closed_toe==true' 같은 식을 평가한다.

    eval 을 쓰지 않는다. && || ! == != true false 와 등록된 플래그만 허용하고,
    모르는 토큰이 나오면 False 를 주면서 이유를 남긴다.
    """
    if not expr:
        return False, "조건식 없음"
    s = str(expr).strip()
    if s.lower() == "always":
        return True, "always"

    unknown = [t for t in _TOKEN.findall(s)
               if t not in flags and t.lower() not in ("true", "false")]
    if unknown:
        return False, f"알 수 없는 플래그: {', '.join(sorted(set(unknown)))}"

    # 논리곱/합만 지원한다. 워크북 규칙은 이 범위를 넘지 않는다.
    def atom(a):
        a = a.strip()
        neg = a.startswith("!")
        if neg:
            a = a[1:].strip()
        if "==" in a or "!=" in a:
            op = "==" if "==" in a else "!="
            lhs, rhs = (x.strip() for x in a.split(op, 1))
            val = flags.get(lhs, False)
            want = rhs.lower() == "true"
            r = (bool(val) == want) if op == "==" else (bool(val) != want)
        else:
            r = bool(flags.get(a, False))
        return (not r) if neg else r

    if "||" in s:
        return any(atom(p) for p in s.split("||")), s
    return all(atom(p) for p in s.split("&&")), s


def build(mapping, geo_ctx, cal, parts, flags=None, construction="Strobel Cemented",
          repairs=None):
    """확정 매핑 + 레시피 -> BOM 라인 목록.

    repairs: {segment_id: {volume_m3, method, confidence_penalty, ...}}
             Mesh QA 를 통과하지 못한 솔리드 파트의 복구 결과.
    """
    flags = {**CONSTRUCTION_FLAGS, **(flags or {})}
    master = catalog.bom_master()
    defaults = catalog.part_defaults()
    lines = []

    # ── 1) 보이는 파트 (실측) ────────────────────────────────────────
    seen = {}
    for m in mapping:
        cp = m["canonical_part"]
        mesh = parts.get(m["segment_id"])
        if mesh is None:
            continue
        info = master.get(cp, {})
        met = part_metrics(mesh, cal, cp)
        key = cp
        seen.setdefault(key, []).append((m, met))

    repairs = repairs or {}
    for cp, group in seen.items():
        info = master.get(cp, {})
        area = sum(g[1]["surface_area_m2"] for g in group)
        qa_block = [r for g in group for r in g[1]["qa"]["blocked_reasons"]]
        segs = [g[0]["segment_id"] for g in group]

        # 부피는 QA 통과분을 먼저 쓰고, 없으면 복구본을 쓴다.
        vols, geo_method, repair_notes = [], "measured", []
        for g in group:
            sid = g[0]["segment_id"]
            if g[1]["volume_m3"] is not None:
                vols.append(g[1]["volume_m3"])
            elif repairs.get(sid, {}).get("ok") and repairs[sid].get("usable", True):
                vols.append(repairs[sid]["volume_m3"])
                geo_method = "repaired"
                sens = repairs[sid].get("sensitivity") or {}
                cv = sens.get("cv")
                repair_notes.append(
                    f"{sid} {repairs[sid]['method']}"
                    + (f", 해상도 CV {cv:.1%} 판정 {sens.get('verdict')}"
                       if cv is not None else ""))
        vol = sum(vols) if vols else None
        role = classify_role(cp, group[0][1]["qa"],
                             repaired=(geo_method == "repaired"))
        if geo_method == "repaired":
            # 복구본은 실측이 아니다. 검사 차단 사유를 지우지 않고 남긴다.
            qa_block = ["복구본 사용 (" + "; ".join(repair_notes)
                        + "). 승인 sole CAD 로 대체 권장"]
        unconfirmed = [g[0]["segment_id"] for g in group if not g[0].get("confirmed")]

        lines.append({
            "line_id": f"V-{info.get('part_id', cp)}",
            "part_id": info.get("part_id"),
            "canonical_part": cp,
            "assembly": info.get("assembly", "Unknown"),
            "visibility": info.get("visibility", "Visible"),
            "origin": "segmentation",
            "segments": segs,
            "qty_per_pair": 1,
            "qty_basis": info.get("qty_basis"),
            "formula_family": info.get("formula_family"),
            "material_spec": defaults.get(cp),
            "geometry_role": role,
            "max_class": ROLE_MAX_CLASS.get(role, "C1"),
            "quantity_basis": "per_shoe",
            "geometry": {
                "surface_area_m2": area,
                "volume_m3": vol,
                "volume_status": ("repaired" if geo_method == "repaired"
                                  else group[0][1]["volume_status"]),
                "method": geo_method,
            },
            # watertight 여부는 부피를 쓰는 라인에서만 문제다. roll/sheet 라인에
            # 붙이면 의미 없는 경고가 쌓여 진짜 차단 사유가 묻힌다.
            "qa_blocked": sorted(set(qa_block)) if cp in VOLUME_ALLOWED_PARTS else [],
            "rule_id": None,
            "approval_status": "unconfirmed" if unconfirmed else "segment_confirmed",
            "needs_confirm": unconfirmed,
        })

    # ── 2) 숨은 파트 (규칙) ──────────────────────────────────────────
    # 세그멘테이션에서 이미 잡힌 파트를 규칙이 또 넣으면 이중계상이 된다.
    # (예: R-016 has_laces -> Lace 는 Lace 세그먼트가 있으면 건너뛴다)
    measured_parts = set(seen)

    for rule in catalog.recipes():
        if rule["construction"] != construction:
            continue
        ok, why = eval_condition(rule["condition"], flags)
        if not ok:
            continue
        cp = rule["add_part"]
        if cp in measured_parts:
            continue
        # 레시피 표기와 BOM 마스터 표기가 조금 다른 경우가 있다.
        info = master.get(cp) or master.get(cp.split("/")[0]) or {}
        meas = geo_ctx.resolve(rule["qty_method"]) if rule["qty_method"] in geo_ctx.METHODS \
            else {"value": 0.0, "unit": "", "method": "count" if rule["qty_method"] == "count"
                  else "blocked", "source": rule["qty_method"], "note": None}

        # 파라미터에 'factory' 가 있으면 공장 입력 없이는 확정 불가.
        meas = apply_rule_scale(meas, rule)

        factory_needed = [k for k, v in rule["parameters"].items() if isinstance(v, str)]
        # 개수 규칙의 per_shoe 는 켤레 환산이 필요하다 (아일렛 12개/짝 = 24/켤레).
        per_shoe = rule["parameters"].get("per_shoe")
        qty_per_pair = (float(per_shoe) * 2 if isinstance(per_shoe, (int, float))
                        and rule["qty_method"] == "count" else 1)
        # 규칙에 숫자를 적어 뒀는데 아무 데서도 안 쓰이면 조용히 틀린다.
        # (실제로 coverage·ratio 가 통째로 무시되고 있었다.)
        ignored = unhandled_params(rule)
        # 규칙이 파트별 두께를 지정하면 그것이 이 디자인의 실제 두께다.
        # 소재 스펙의 두께는 그 소재의 대표값이라 파트마다 다를 수 있다.
        rule_thk = rule["parameters"].get("thickness_mm")
        thk_override = float(rule_thk) if isinstance(rule_thk, (int, float)) else None

        lines.append({
            "line_id": f"H-{rule['rule_id']}",
            "part_id": info.get("part_id"),
            "canonical_part": cp,
            "assembly": info.get("assembly") or rule.get("assembly") or "Hidden",
            "visibility": info.get("visibility", "Hidden"),
            "origin": "construction_rule",
            "segments": [],
            "qty_per_pair": qty_per_pair,
            "qty_basis": info.get("qty_basis") or rule["qty_method"],
            "formula_family": info.get("formula_family"),
            "material_spec": defaults.get(cp) or defaults.get(cp.split("/")[0]),
            "geometry": {
                "surface_area_m2": meas["value"] if meas["unit"] == "m2" else None,
                "length_m": meas["value"] if meas["unit"] == "m" else None,
                "volume_m3": None,
                "volume_status": None,
                "method": meas["method"],
                "source": meas["source"],
                "note": meas.get("note"),
            },
            "qa_blocked": [],
            # 박스·폴리백처럼 개수로 사는 품목에 표면 역할을 붙이면
            # 지오메트리 불확실성이 없는 라인까지 C1 로 눌린다 (외부 검토 지적).
            "geometry_role": ("fixed_quantity" if rule["qty_method"] == "count"
                              else "curve_or_trim" if meas["unit"] == "m"
                              else "surface_region"),
            "max_class": ("C2" if rule["qty_method"] == "count" else "C1"),
            "quantity_basis": ("per_pair" if rule["qty_method"] == "count"
                               else "per_shoe"),
            "rule_id": rule["rule_id"],
            "rule_condition": rule["condition"],
            "rule_parameters": rule["parameters"],
            # 규칙에 적혔지만 계산에 안 쓰인 파라미터. 비어 있어야 정상이다.
            "rule_params_ignored": [k for k in ignored if k != "thickness_mm"],
            "thickness_mm_override": thk_override,
            "rule_evidence": rule["evidence"],
            "approval_role": rule["approval_role"],
            "factory_inputs_required": factory_needed,
            # Hidden BOM 은 엔지니어 승인 전에는 C2 로 못 간다 (계획서 §12).
            "approval_status": "rule_proposed",
        })

    lines.sort(key=lambda d: (d["assembly"], d["canonical_part"]))
    return lines
