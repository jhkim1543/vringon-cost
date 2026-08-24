# -*- coding: utf-8 -*-
"""소재별 Gross Consumption (계획서 §10).

핵심 규칙은 하나다. 순 지오메트리는 구매 수량이 아니다.
패턴 여유·네스팅 수율·공정 수율을 거쳐야 실제 구매량이 된다.
그리고 어느 계수가 가정인지 결과에 그대로 달고 다닌다.
"""
import catalog


def _p(spec_id, key, default):
    return catalog.param(spec_id, key, default)


def _blocked(reason, **extra):
    d = {"gross_qty": None, "uom": None, "blocked": [reason], "assumptions": []}
    d.update(extra)
    return d



# 기하에서 나온 수량은 신발 '한 짝' 기준이다. 원가는 켤레 기준이므로 2를 곱한다.
# 반대로 박스·레이스·행택처럼 켤레 단위로 사는 품목은 곱하지 않는다.
GEOMETRY_FORMS = {"roll", "sheet", "sheet_m2", "molded", "chemical", "thread", "length"}
PER_PAIR_UOM = {"pair"}


def _compute_one(line, price_uom=None):
    """한 짝 기준 소요량. 켤레 환산은 compute() 가 한다.

    반환: {gross_qty, uom, net, steps[], assumptions[], blocked[], pair_factor}
      steps       계산 과정 (UI 근거 패널에 그대로 표시)
      assumptions 가정으로 쓴 파라미터 (source != workbook)
      blocked     계산을 막은 사유
    """
    spec = line.get("material_spec")
    if not spec:
        return _blocked(f"{line['canonical_part']}: 소재가 배정되지 않음")

    sp = catalog.material_specs().get(spec)
    if not sp:
        return _blocked(f"소재 스펙 '{spec}' 정의 없음")

    form = sp.get("form")
    geo = line.get("geometry") or {}
    steps, assumptions, blocked = [], [], []

    def take(key, default, label):
        p = _p(spec, key, default)
        if p["value"] is None:
            blocked.append(f"{spec}.{key} 미정의")
            return None
        if p["source"] in ("assumption", "factory_required", "public_reference"):
            assumptions.append({"param": key, "value": p["value"],
                                "source": p["source"], "note": p.get("note")})
        if p["source"] == "factory_required":
            blocked.append(f"{spec}.{key} 는 공장 확인 필요 (C2 차단)")
        steps.append(f"{label} = {p['value']}  [{p['source']}]")
        return p["value"]

    # ── roll: 가격 UOM 이 m (선형 미터) ──────────────────────────────
    if form == "roll":
        net = geo.get("surface_area_m2")
        if not net:
            return _blocked(f"{line['canonical_part']}: 면적 없음")
        steps.append(f"순 표면적 = {net:.6f} m²  [{geo.get('method','?')}]")
        pf = take("pattern_factor", 1.15, "패턴 여유")
        w = take("usable_width_m", 1.5, "유효 폭(m)")
        ny = take("nesting_yield", 0.82, "네스팅 수율")
        py = take("process_yield", 0.97, "공정 수율")
        if None in (pf, w, ny, py):
            return {"gross_qty": None, "uom": "m", "net": net, "steps": steps,
                    "assumptions": assumptions, "blocked": blocked}
        pattern = net * pf
        gross = pattern / (w * ny * py)
        steps.append(f"패턴 면적 = {net:.6f} × {pf} = {pattern:.6f} m²")
        steps.append(f"총 소요 = {pattern:.6f} / ({w} × {ny} × {py}) = {gross:.6f} m")
        return {"gross_qty": gross, "uom": "m", "net": net, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── sheet: 가격 UOM 이 sheet ─────────────────────────────────────
    if form == "sheet":
        net = geo.get("surface_area_m2")
        if not net:
            return _blocked(f"{line['canonical_part']}: 면적 없음")
        steps.append(f"순 표면적 = {net:.6f} m²  [{geo.get('method','?')}]")
        pf = take("pattern_factor", 1.10, "패턴 여유")
        sa = take("sheet_area_m2", 1.5, "시트 면적(m²)")
        ny = take("nesting_yield", 0.80, "네스팅 수율")
        py = take("process_yield", 0.95, "공정 수율")
        if None in (pf, sa, ny, py):
            return {"gross_qty": None, "uom": "sheet", "net": net, "steps": steps,
                    "assumptions": assumptions, "blocked": blocked}
        gross = (net * pf) / (sa * ny * py)
        steps.append(f"총 소요 = ({net:.6f} × {pf}) / ({sa} × {ny} × {py}) = {gross:.6f} sheet")
        return {"gross_qty": gross, "uom": "sheet", "net": net, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── sheet_m2: 가격 UOM 이 m² ────────────────────────────────────
    if form == "sheet_m2":
        net = geo.get("surface_area_m2")
        if not net:
            return _blocked(f"{line['canonical_part']}: 면적 없음")
        steps.append(f"순 표면적 = {net:.6f} m²")
        pf = take("pattern_factor", 1.12, "패턴 여유")
        ny = take("nesting_yield", 0.80, "네스팅 수율")
        py = take("process_yield", 0.96, "공정 수율")
        if None in (pf, ny, py):
            return {"gross_qty": None, "uom": "m²", "net": net, "steps": steps,
                    "assumptions": assumptions, "blocked": blocked}
        gross = net * pf / (ny * py)
        steps.append(f"총 소요 = {net:.6f} × {pf} / ({ny} × {py}) = {gross:.6f} m²")
        return {"gross_qty": gross, "uom": "m²", "net": net, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── molded: 검증된 닫힌 부피만 질량으로 바꾼다 (계획서 §5.5) ──────
    if form == "molded":
        vol = geo.get("volume_m3")
        status = geo.get("volume_status")
        if vol is None:
            reason = status or "부피 없음"
            return _blocked(
                f"{line['canonical_part']}: 부피 계산 차단 ({reason})",
                uom="kg", hint="mesh 복구 / sole CAD / 승인 recipe 비율 필요")
        steps.append(f"검증 부피 = {vol:.9f} m³  [watertight QA 통과]")
        dens = take("molded_density_kg_m3", None, "완성 성형 밀도(kg/m³)")
        py = take("process_yield", 0.88, "공정 수율")
        if None in (dens, py):
            return {"gross_qty": None, "uom": "kg", "net": vol, "steps": steps,
                    "assumptions": assumptions, "blocked": blocked}
        net_mass = vol * dens
        gross = net_mass / py
        steps.append(f"순 질량 = {vol:.9f} × {dens} = {net_mass:.6f} kg")
        steps.append(f"투입 질량 = {net_mass:.6f} / {py} = {gross:.6f} kg")
        return {"gross_qty": gross, "uom": "kg", "net": vol, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── chemical: 도포 면적 × 습도포량 × 도포횟수 / 전이효율 ──────────
    if form == "chemical":
        area = geo.get("surface_area_m2")
        if not area:
            return _blocked(f"{line['canonical_part']}: 도포 면적 없음", uom="kg")
        steps.append(f"도포 면적 = {area:.6f} m²  [{geo.get('method','?')}]")
        wc = take("wet_coat_kg_m2", 0.10, "습도포량(kg/m²)")
        co = take("coats", 1, "도포 횟수")
        te = take("transfer_efficiency", 0.85, "전이 효율")
        if None in (wc, co, te):
            return {"gross_qty": None, "uom": "kg", "net": area, "steps": steps,
                    "assumptions": assumptions, "blocked": blocked}
        gross = area * wc * co / te
        steps.append(f"총 소요 = {area:.6f} × {wc} × {co} / {te} = {gross:.6f} kg")
        return {"gross_qty": gross, "uom": "kg", "net": area, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── thread: 봉제선 길이 × 소요배수 × 손실 ─────────────────────────
    if form == "thread":
        seam = geo.get("length_m")
        if not seam:
            return _blocked(f"{line['canonical_part']}: 봉제선 길이 없음", uom="kg")
        steps.append(f"봉제선 길이 = {seam:.4f} m  [{geo.get('method','?')}]")
        sf = take("stitch_consumption_factor", 3.0, "스티치 소요 배수")
        wf = take("waste_factor", 1.10, "손실 계수")
        tex = take("linear_density_tex", 40.0, "선밀도(tex)")
        if None in (sf, wf, tex):
            return {"gross_qty": None, "uom": "kg", "net": seam, "steps": steps,
                    "assumptions": assumptions, "blocked": blocked}
        length = seam * sf * wf
        mass = length * tex / 1_000_000.0        # tex = g/1000m -> kg
        steps.append(f"실 길이 = {seam:.4f} × {sf} × {wf} = {length:.4f} m")
        steps.append(f"질량 = {length:.4f} m × {tex} tex / 1e6 = {mass:.6f} kg")
        return {"gross_qty": mass, "uom": "kg", "net": seam, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── length: 그대로 미터 ─────────────────────────────────────────
    if form == "length":
        L = geo.get("length_m")
        if not L:
            return _blocked(f"{line['canonical_part']}: 길이 없음", uom="m")
        wf = take("waste_factor", 1.05, "손실 계수") or 1.05
        steps.append(f"순 길이 = {L:.4f} m")
        return {"gross_qty": L * wf, "uom": "m", "net": L, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── mass: 개당 질량이 있어야 kg 단가와 곱할 수 있다 ────────────────
    if form == "mass":
        qty = float(line.get("qty_per_pair") or 1)
        upm = take("mass_per_pair_kg", None, "켤레당 질량(kg)")
        if upm is None:
            return {"gross_qty": None, "uom": "kg", "net": qty, "steps": steps,
                    "assumptions": assumptions,
                    "blocked": blocked + [f"{spec}: 켤레당 질량 미정의 (kg 단가와 곱할 수 없음)"]}
        gross = qty * upm
        steps.append(f"총 소요 = {qty} × {upm} kg = {gross:.6f} kg")
        return {"gross_qty": gross, "uom": "kg", "net": qty, "steps": steps,
                "assumptions": assumptions, "blocked": blocked}

    # ── count: 개수 기준 ────────────────────────────────────────────
    if form == "count":
        qty = float(line.get("qty_per_pair") or 1)
        steps.append(f"수량 = {qty} {sp.get('uom', 'ea')} / 켤레")
        return {"gross_qty": qty, "uom": sp.get("uom", "ea"), "net": qty,
                "steps": steps, "assumptions": assumptions, "blocked": blocked}

    return _blocked(f"소재 형태 '{form}' 미지원")


def pair_factor(line, spec_id):
    """한 짝 -> 켤레 환산 계수."""
    if line.get("pair_factor") is not None:
        return float(line["pair_factor"])
    sp = catalog.material_specs().get(spec_id) or {}
    if sp.get("form") in GEOMETRY_FORMS:
        return 2.0                      # 좌/우 두 짝
    if sp.get("uom") in PER_PAIR_UOM:
        return 1.0                      # 이미 켤레 단위로 판다
    return float(sp.get("units_per_pair", 1.0))


def compute(line, price_uom=None):
    """켤레 기준 총 구매 수량.

    3D 는 신발 한 짝이고 원가는 켤레 기준이다. 이 환산을 빼먹으면
    소재비가 정확히 절반으로 나온다.
    """
    r = _compute_one(line, price_uom)
    spec = line.get("material_spec")
    if not spec or r.get("gross_qty") is None:
        return {**r, "pair_factor": None}
    f = pair_factor(line, spec)
    if f != 1.0:
        r = dict(r)
        r["steps"] = list(r.get("steps") or []) + [
            f"켤레 환산 = {r['gross_qty']:.6f} × {f:g} = {r['gross_qty']*f:.6f} {r.get('uom','')}"
        ]
        r["gross_qty"] = r["gross_qty"] * f
    return {**r, "pair_factor": f}
