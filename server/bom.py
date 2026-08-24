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
    "adhesive_requires_hardener": False,
    "has_laces": True,
    "has_print": False,
    "midsole_is_painted": False,
    "market_requires_polybag": True,
}

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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
            elif repairs.get(sid, {}).get("ok"):
                vols.append(repairs[sid]["volume_m3"])
                geo_method = "repaired"
                repair_notes.append(f"{sid}: {repairs[sid]['method']}")
        vol = sum(vols) if vols else None
        if geo_method == "repaired":
            # 복구본은 측정이 아니다. QA 차단 사유를 지우지 않고 남긴다.
            qa_block = [f"복구본 사용 ({'; '.join(repair_notes)}) — 승인 sole CAD 권장"]
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

    # 세그멘테이션에서 이미 잡힌 파트를 규칙이 또 넣으면 이중계상이 된다.
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
        if cp in measured_parts:
            continue
        # 레시피 표기와 BOM 마스터 표기가 조금 다른 경우가 있다.
        info = master.get(cp) or master.get(cp.split("/")[0]) or {}
        meas = geo_ctx.resolve(rule["qty_method"]) if rule["qty_method"] in geo_ctx.METHODS \
            else {"value": 0.0, "unit": "", "method": "count" if rule["qty_method"] == "count"
                  else "blocked", "source": rule["qty_method"], "note": None}

        # 파라미터에 'factory' 가 있으면 공장 입력 없이는 확정 불가.
        factory_needed = [k for k, v in rule["parameters"].items() if isinstance(v, str)]

        lines.append({
            "line_id": f"H-{rule['rule_id']}",
            "part_id": info.get("part_id"),
            "canonical_part": cp,
            "assembly": info.get("assembly", "Hidden"),
            "visibility": info.get("visibility", "Hidden"),
            "origin": "construction_rule",
            "segments": [],
            "qty_per_pair": 1,
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
            "geometry_role": ("curve_or_trim" if meas["unit"] == "m"
                              else "surface_region"),
            "max_class": "C1",
            "quantity_basis": ("per_pair" if rule["qty_method"] == "count"
                               else "per_shoe"),
            "rule_id": rule["rule_id"],
            "rule_condition": rule["condition"],
            "rule_parameters": rule["parameters"],
            "rule_evidence": rule["evidence"],
            "approval_role": rule["approval_role"],
            "factory_inputs_required": factory_needed,
            # Hidden BOM 은 엔지니어 승인 전에는 C2 로 못 간다 (계획서 §12).
            "approval_status": "rule_proposed",
        })

    lines.sort(key=lambda d: (d["assembly"], d["canonical_part"]))
    return lines
