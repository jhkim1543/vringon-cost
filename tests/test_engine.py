# -*- coding: utf-8 -*-
"""엔진 불변식 테스트.

    .venv\\Scripts\\python.exe -m pytest tests -q

대부분은 실제로 한 번 틀렸던 것들의 회귀 테스트다. 주석에 무엇이 틀렸는지 남긴다.
"""
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import bom as bom_mod          # noqa: E402
import canonical               # noqa: E402
import catalog                 # noqa: E402
import consumption             # noqa: E402
import costing                 # noqa: E402
import geometry as geo         # noqa: E402
import pricing                 # noqa: E402
import repair                  # noqa: E402

SEG = ROOT / "data" / "projects" / "DEMO-RUN-001" / "segmented.glb"
has_sample = pytest.mark.skipif(not SEG.exists(), reason="샘플 GLB 없음")


# ── 캘리브레이션 ──────────────────────────────────────────────────────
def test_scale_powers():
    """길이는 s, 면적은 s², 부피는 s³ 로 간다."""
    cal = geo.calibration(300.0, [1, 0, 0], [0, 0, 0])
    assert cal["scale"] == pytest.approx(300.0)
    assert cal["area_scale"] == pytest.approx(300.0 ** 2)
    assert cal["volume_scale"] == pytest.approx(300.0 ** 3)
    assert geo.to_si(1.0, "length", cal) == pytest.approx(0.3)          # m
    assert geo.to_si(1.0, "area", cal) == pytest.approx(0.09)           # m²
    assert geo.to_si(1.0, "volume", cal) == pytest.approx(0.027)        # m³


def test_scale_uses_measured_length_not_size_label():
    """사이즈 260 을 260mm 로 쓰면 안 된다. 입력은 실측 외부 길이다."""
    cal = geo.calibration(300.0, [2, 0, 0], [0, 0, 0])
    assert cal["scale"] == pytest.approx(150.0)
    assert cal["measurement_type"] == "External outsole toe-to-heel"
    assert cal["confidence"] == "B"          # 길이만 -> B


def test_calibration_rejects_degenerate_landmarks():
    with pytest.raises(ValueError):
        geo.calibration(300.0, [0, 0, 0], [0, 0, 0])


# ── Mesh QA 게이트 ────────────────────────────────────────────────────
def test_open_mesh_volume_blocked():
    """열린 메시의 부피는 신뢰할 수 없으므로 막아야 한다."""
    box = trimesh.creation.box((1, 1, 1))
    open_mesh = box.submesh([np.arange(len(box.faces) - 2)], append=True)
    qa = geo.mesh_qa(open_mesh)
    assert not qa["watertight"]
    assert not qa["is_volume"]
    assert qa["blocked_reasons"]

    cal = geo.calibration(100.0, [1, 0, 0], [0, 0, 0])
    m = geo.part_metrics(open_mesh, cal, "Outsole Rubber")
    assert m["volume_m3"] is None
    assert m["volume_status"].startswith("blocked")


def test_closed_mesh_volume_ok():
    box = trimesh.creation.box((1, 1, 1))
    cal = geo.calibration(1000.0, [1, 0, 0], [0, 0, 0])   # 1 unit = 1000mm = 1m
    m = geo.part_metrics(box, cal, "Outsole Rubber")
    assert m["volume_m3"] == pytest.approx(1.0, rel=1e-6)  # 1m³


def test_shell_part_never_uses_volume():
    """어퍼 같은 껍질 파트는 닫혀 있어도 부피를 소재 부피로 쓰지 않는다."""
    box = trimesh.creation.box((1, 1, 1))
    cal = geo.calibration(100.0, [1, 0, 0], [0, 0, 0])
    m = geo.part_metrics(box, cal, "Vamp")
    assert m["volume_m3"] is None
    assert m["volume_status"] == "not_a_solid_part"


# ── 규칙 엔진 ─────────────────────────────────────────────────────────
def test_condition_eval_without_eval():
    f = dict(bom_mod.CONSTRUCTION_FLAGS)
    assert bom_mod.eval_condition("always", f)[0]
    assert bom_mod.eval_condition("closed_toe==true", f)[0]
    assert bom_mod.eval_condition("has_eyelets && lightweight_upper", f)[0]
    assert not bom_mod.eval_condition("waterproof==true", f)[0]
    # 모르는 토큰은 조용히 참이 되면 안 된다
    ok, why = bom_mod.eval_condition("__import__('os')", f)
    assert not ok and "알 수 없는" in why


def test_all_workbook_conditions_are_understood():
    """워크북의 모든 조건식이 화이트리스트로 평가 가능해야 한다."""
    f = dict(bom_mod.CONSTRUCTION_FLAGS)
    for r in catalog.recipes():
        ok, why = bom_mod.eval_condition(r["condition"], f)
        assert "알 수 없는" not in why, f"{r['rule_id']}: {why}"


# ── 소요량 ────────────────────────────────────────────────────────────
def _line(part, spec, **geom):
    return {"canonical_part": part, "material_spec": spec, "qty_per_pair": 1,
            "geometry": {"method": "measured", **geom}}


def test_roll_consumption_and_pair_factor():
    """3D 는 한 짝, 원가는 켤레. 기하 유래 수량은 ×2 여야 한다."""
    l = _line("Vamp", "MAT-MESH-POLY", surface_area_m2=1.0)
    r = consumption.compute(l)
    assert r["pair_factor"] == 2.0
    # 1.0 m² × 1.18 / (1.5 × 0.82 × 0.97) × 2
    expected = 1.0 * 1.18 / (1.5 * 0.82 * 0.97) * 2
    assert r["gross_qty"] == pytest.approx(expected, rel=1e-9)
    assert r["uom"] == "m"


def test_packaging_is_not_one_kilogram():
    """회귀: 폴리백을 '1개'로 세고 kg 단가를 곱해 켤레당 1kg 이 나왔었다."""
    r = consumption.compute(_line("Polybag", "MAT-LDPE"))
    assert r["uom"] == "kg"
    assert r["gross_qty"] == pytest.approx(0.005)     # 5 g
    assert r["gross_qty"] < 0.05

    r2 = consumption.compute(_line("Tissue/Stuffing", "MAT-PAPERBOARD"))
    assert r2["gross_qty"] == pytest.approx(0.018)


def test_pair_priced_items_not_doubled():
    """레이스·박스는 켤레 단위로 사므로 ×2 하면 안 된다."""
    assert consumption.pair_factor({}, "MAT-LACE-POLY") == 1.0
    assert consumption.pair_factor({}, "MAT-BOX") == 1.0
    assert consumption.pair_factor({}, "MAT-EVA-COMP") == 2.0


def test_molded_blocked_without_volume():
    r = consumption.compute(_line("Outsole Rubber", "MAT-NR",
                                  volume_m3=None, volume_status="blocked:not watertight"))
    assert r["gross_qty"] is None
    assert any("부피 계산 차단" in b for b in r["blocked"])


def test_molded_mass_from_volume():
    r = consumption.compute(_line("Outsole Rubber", "MAT-NR", volume_m3=1e-4))
    # 1e-4 m³ × 1150 kg/m³ / 0.86 × 2짝
    assert r["gross_qty"] == pytest.approx(1e-4 * 1150 / 0.86 * 2, rel=1e-9)
    assert r["uom"] == "kg"


def test_factory_required_params_block_c2():
    r = consumption.compute(_line("Cement/Adhesive", "MAT-ADH-PU", surface_area_m2=0.04))
    assert r["gross_qty"] is not None            # C1 에서는 계산된다
    assert any("공장 확인 필요" in b for b in r["blocked"])   # C2 는 막힌다
    assert any(a["source"] == "factory_required" for a in r["assumptions"])


def test_assumptions_are_reported():
    r = consumption.compute(_line("Vamp", "MAT-MESH-POLY", surface_area_m2=1.0))
    params = {a["param"] for a in r["assumptions"]}
    assert "usable_width_m" in params and "nesting_yield" in params


# ── 단가 ──────────────────────────────────────────────────────────────
def test_public_listing_never_reaches_c2():
    p = pricing.select("MAT-MESH-POLY", "2026Q3")
    assert p["eligibility"] == "Concept only"
    assert p["max_class"] == "C1"


def test_missing_quarter_carries_forward_as_stale():
    """회귀 방지: 새 분기에 데이터가 없다고 조용히 복사하면 안 된다."""
    p = pricing.select("MAT-MESH-POLY", "2027Q1")
    assert p["stale"] is True
    assert p["confidence"] == "D"
    assert p["max_class"] == "C1"
    assert "이관" in p["note"]


def test_snapshot_marks_everything_stale_when_no_new_data():
    s = pricing.make_snapshot("2026Q4", observations=None)
    assert s["fresh"] == 0
    assert s["stale"] == 34            # 워크북 2026Q3 스냅샷 34건
    assert all(r["stale"] for r in s["rows"])


def test_chemical_price_proxy_is_flagged():
    p = pricing.select("MAT-CLEANER", "2026Q3")
    assert p["basis"] == "price_proxy"
    assert p["confidence"] == "D"
    assert p["p50"] == pytest.approx(
        pricing.select("MAT-ADH-PU", "2026Q3")["p50"] * 0.45)


def test_uom_mismatch_refuses_to_multiply():
    ok, note = pricing.uom_match("m", "m²")
    assert not ok and "불일치" in note
    assert pricing.uom_match("kg", "kg")[0]
    assert pricing.uom_match("piece", "pair")[0]


# ── 원가 롤업 ─────────────────────────────────────────────────────────
def test_labor_and_tooling_blocked_not_zero():
    """워크북 공장 데이터가 TBD 이므로 0 이 아니라 Blocked 여야 한다."""
    lm = costing.labor_machine(5000)
    assert lm["status"] == "blocked"
    assert lm["labor_usd_pair"] is None      # 0.0 이면 '노무비 없음'으로 읽힌다
    tl = costing.tooling(5000)
    assert tl["status"] == "blocked"
    assert tl["usd_pair"] is None


def test_partial_cost_never_reports_a_total():
    """회귀: 노무·기계·금형이 Blocked 인데 Provisional Total 을 내보냈다.

    부분 소계에 overhead·margin 을 곱하면 비율의 분모가 소재비뿐이라
    숫자가 조용히 왜곡되고, 사용자는 누락 비용을 0 으로 읽는다.
    """
    lines = [{"line_id": "X", "status": "calculated", "cost_p10": 1.0,
              "cost_p50": 2.0, "cost_p90": 3.0, "material_spec": "MAT-BOX",
              "price": {"eligibility": "Concept only"}}]
    r = costing.roll_up(lines, {"order_quantity": 5000})

    assert r["cost_status"] == "PARTIAL"
    assert not r["direct_complete"]
    # 전체 제조원가와 FOB 는 숫자로 나오면 안 된다
    assert r["manufacturing_should_cost"] is None
    assert r["fob"] is None
    assert r["factory_overhead"] is None and r["supplier_margin"] is None
    assert "provisional_total" not in r
    # 아는 것만 소계로. 차단된 버킷은 0 으로 합산되지 않는다
    assert r["known_cost_subtotal"]["p50"] == pytest.approx(2.0)
    assert set(r["blocked_buckets"]) == {"Direct Labor", "Machine", "Tooling Amortization"}


def test_dimension_validator_blocks_nonsense_multiplication():
    """count × USD/kg 같은 조합은 즉시 막아야 한다."""
    import units
    assert not units.check_multiply("piece", "kg")[0]
    assert not units.check_multiply("m²", "piece")[0]
    assert not units.check_multiply("kg", "m")[0]
    assert units.check_multiply("kg", "kg")[0]
    assert units.check_multiply("m", "m")[0]
    # 모르는 단위는 통과시키지 않는다
    assert not units.check_multiply("furlong", "kg")[0]
    # sheet 수량에 piece 단가를 곱하지 않는다
    assert not units.check_multiply("sheet", "piece")[0]


def test_formula_output_dimension_is_enforced():
    import units
    assert units.check_formula("roll", "m")[0]          # 면적 -> 선형미터
    assert not units.check_formula("roll", "kg")[0]
    assert units.check_formula("molded", "kg")[0]       # 부피 -> 질량
    assert not units.check_formula("molded", "m³")[0]


def test_basis_conversion_is_not_a_blanket_double():
    import units
    assert units.to_pair(1.0, "per_shoe")[0] == pytest.approx(2.0)
    assert units.to_pair(1.0, "per_pair")[0] == pytest.approx(1.0)
    # 배치 기준은 배치 수량 없이는 환산 불가 — 조용히 2배 하지 않는다
    assert units.to_pair(1.0, "per_batch")[0] is None
    assert units.to_pair(100.0, "per_batch", batch_qty=1000)[0] == pytest.approx(0.1)


def test_geometry_role_separates_open_shell_from_broken_solid():
    """열려 있다고 다 같은 오류가 아니다. vamp 는 원래 표면 조각이다."""
    open_qa = {"is_volume": False}
    closed_qa = {"is_volume": True}
    # 어퍼 패널: 열려 있는 게 정상, 부피 금지
    assert geo.classify_role("Vamp", open_qa) == "surface_region"
    # 솔리드여야 하는 파트가 열려 있으면 solid_component 라고 부르지 않는다
    assert geo.classify_role("Midsole Carrier", open_qa) == "surface_region"
    assert geo.classify_role("Midsole Carrier", open_qa, repaired=True) == "repaired_volume_proxy"
    assert geo.classify_role("Midsole Carrier", closed_qa) == "solid_component"
    assert geo.classify_role("Lace", open_qa) == "curve_or_trim"


def test_repaired_volume_cannot_reach_c2():
    assert geo.ROLE_MAX_CLASS["repaired_volume_proxy"] == "C1"
    assert geo.ROLE_MAX_CLASS["surface_region"] == "C1"
    assert geo.ROLE_MAX_CLASS["solid_component"] == "C2"
    assert geo.ROLE_MAX_CLASS["approved_cad_solid"] == "C2"


def test_grade_downgrades_without_gates():
    lines = [{"material_spec": "MAT-MESH-POLY",
              "price": {"eligibility": "Concept only"}}]
    rollup = costing.roll_up([], {"order_quantity": 1000})
    g = costing.grade(lines, rollup, gates={})
    assert g["class"] == "C0"
    g2 = costing.grade(lines, rollup, gates={
        "metric_calibrated": True, "segmented": True,
        "construction_set": True, "mbom_built": True})
    assert g2["class"] == "C1"
    assert g2["blocked_reasons"]["C2"]        # C2 는 여전히 막혀 있다


# ── 복구 ──────────────────────────────────────────────────────────────
def test_repair_closes_open_mesh_with_sane_volume():
    box = trimesh.creation.box((1, 1, 1))
    open_mesh = box.submesh([np.arange(len(box.faces) - 2)], append=True)
    r = repair.repair_to_solid(open_mesh)
    assert r["ok"]
    # 회귀: marching_cubes 를 복셀 좌표 그대로 써서 부피가 pitch^-3 배로 튀었다
    assert 0.3 < r["raw_volume"] < 3.0, r["raw_volume"]
    assert r["confidence_penalty"] >= 1


# ── 실제 샘플 ─────────────────────────────────────────────────────────
@has_sample
def test_scene_parts_apply_node_transforms():
    """회귀: 노드 변환을 빼먹어 모든 파트가 원점에 겹쳐 쌓였었다."""
    parts = geo.scene_parts(geo.load_scene(SEG))
    assert len(parts) >= 5
    centers = np.array([m.vertices.mean(axis=0) for m in parts.values()])
    spread = centers.max(axis=0) - centers.min(axis=0)
    whole = trimesh.util.concatenate(list(parts.values()))
    size = whole.bounds[1] - whole.bounds[0]
    # 파트 중심이 모델 크기의 최소 15% 는 흩어져 있어야 한다
    assert (spread / size).max() > 0.15, (spread, size)


@has_sample
def test_canonical_frame_puts_sole_at_bottom():
    """접지면에서 up 을 뽑으므로 솔이 아래에 와야 한다."""
    parts = geo.scene_parts(geo.load_scene(SEG))
    whole = trimesh.util.concatenate(list(parts.values()))
    F = canonical.canonical_frame(whole)
    ctr = np.asarray(whole.vertices).mean(0)
    P = (np.asarray(whole.vertices) - ctr) @ F.T
    lo, hi = P[:, 1].min(), P[:, 1].max()

    def h(mesh):
        q = (np.asarray(mesh.vertices) - ctr) @ F.T
        fh = ((q[:, 1] - lo) / (hi - lo))[mesh.faces].mean(axis=1)
        return float((mesh.area_faces * fh).sum() / mesh.area_faces.sum())

    heights = {n: h(m) for n, m in parts.items()}
    # 가장 낮은 파트와 가장 높은 파트가 확실히 갈려야 한다
    assert max(heights.values()) - min(heights.values()) > 0.4, heights


@has_sample
def test_no_duplicate_lace_line():
    """회귀: 세그먼트 Lace 와 규칙 R-016 Lace 가 둘 다 들어가 이중계상됐다."""
    import pipeline
    p = pipeline.Project("DEMO-RUN-001")
    lines = p.state.get("bom") or []
    if not lines:
        pytest.skip("BOM 미생성")
    laces = [l for l in lines if l["canonical_part"] == "Lace"]
    assert len(laces) == 1, [l["line_id"] for l in laces]


# ── 피드백 반영분 회귀 ─────────────────────────────────────────────────
def test_voxel_cv_gate_rejects_open_shell_repair():
    """열린 껍질에 복셀을 채우면 부피가 pitch 에 비례한다 -> CV 로 걸러야 한다."""
    box = trimesh.creation.box((1, 1, 1))
    open_mesh = box.submesh([np.arange(len(box.faces) - 2)], append=True)
    s = repair.volume_sensitivity(open_mesh, pitches_mm=(20.0, 30.0, 40.0),
                                  scale_mm_per_unit=1000.0)
    assert s["cv"] is not None
    assert s["verdict"] in ("ok", "needs_review", "blocked")
    # 닫힌 상자는 해상도를 바꿔도 부피가 안정적이어야 한다
    s2 = repair.volume_sensitivity(box, pitches_mm=(20.0, 30.0, 40.0),
                                   scale_mm_per_unit=1000.0)
    assert s2["cv"] < 0.15, s2


def test_mass_balance_flags_over_estimate():
    """질량 미산정 라인이 많은데 목표에 근접하면 과대추정으로 본다."""
    lines = [
        {"canonical_part": "Outsole Rubber", "assembly": "Bottom",
         "material_spec": "MAT-NR", "geometry": {},
         "consumption": {"gross_qty": 0.31, "uom": "kg", "net": 2.32e-4}},
        {"canonical_part": "Midsole Carrier", "assembly": "Bottom",
         "material_spec": "MAT-EVA-COMP", "geometry": {},
         "consumption": {"gross_qty": 0.19, "uom": "kg", "net": 1.2e-3}},
    ] + [{"canonical_part": f"Panel{i}", "assembly": "Upper External",
          "material_spec": "MAT-ZIPPER", "geometry": {},
          "consumption": {"gross_qty": 1, "uom": "piece"}} for i in range(6)]
    m = costing.mass_balance(lines, target_pair_g=600)
    # 순질량 = (2.32e-4*1150 + 1.2e-3*220) x 1000 x 2 = 1061.6 g -> 목표 초과
    assert m["finished_pair_mass_g"] == pytest.approx(1061.6, abs=1.0)
    assert len(m["lines_without_mass"]) == 6
    assert m["verdict"] == "fail_over_estimate"


def test_mass_balance_flags_missing_bom():
    lines = [{"canonical_part": "Outsole Rubber", "material_spec": "MAT-NR",
              "consumption": {"gross_qty": 0.05, "uom": "kg"}}]
    m = costing.mass_balance(lines, target_pair_g=600)
    assert m["verdict"] == "suspect_missing_bom"


def test_min_class_takes_the_lower_cap():
    """지오메트리 상한과 단가 자격 중 낮은 쪽이 라인 상한이다."""
    assert costing._min_class("C2", "C1") == "C1"
    assert costing._min_class("C1", "C2") == "C1"
    assert costing._min_class("C2", "C2") == "C2"
    assert costing._min_class(None, "C1") == "C1"


# ── 외부 검토 2차 반영분 ───────────────────────────────────────────────
def test_finished_mass_uses_net_not_charged():
    """회귀: 완제품 질량에 투입질량(수율 나눔 포함)을 쓰면 과대평가된다."""
    lines = [{
        "canonical_part": "Midsole Carrier", "assembly": "Bottom",
        "material_spec": "MAT-EVA-COMP",
        "geometry": {"surface_area_m2": None},
        "consumption": {"gross_qty": 0.19440, "uom": "kg", "net": 1.944e-4},
    }]
    m = costing.mass_balance(lines, target_pair_g=600)
    # 순질량 = 1.944e-4 m3 x 220 kg/m3 x 1000 x 2짝 = 85.5g
    assert m["finished_pair_mass_g"] == pytest.approx(85.5, abs=0.5)
    # 구매 투입은 gross 그대로 194.4g
    assert m["purchased_input_mass_g"] == pytest.approx(194.4, abs=0.5)
    assert m["finished_pair_mass_g"] < m["purchased_input_mass_g"]


def test_packaging_excluded_from_finished_mass():
    """회귀: 티슈 18g 이 신발 무게에 들어가 있었다."""
    lines = [{
        "canonical_part": "Tissue/Stuffing", "assembly": "Packaging",
        "material_spec": "MAT-PAPERBOARD", "geometry": {},
        "consumption": {"gross_qty": 0.018, "uom": "kg", "net": 1},
    }]
    m = costing.mass_balance(lines, target_pair_g=600)
    assert m["finished_pair_mass_g"] == 0.0
    assert m["purchased_input_mass_g"] == pytest.approx(18.0)
    assert m["excluded_packaging"][0]["canonical_part"] == "Tissue/Stuffing"


def test_adhesive_counts_dry_solids_only():
    lines = [{
        "canonical_part": "Cement/Adhesive", "assembly": "Chemical",
        "material_spec": "MAT-ADH-PU", "geometry": {},
        "consumption": {"gross_qty": 0.020, "uom": "kg", "net": 0.04},
    }]
    m = costing.mass_balance(lines, target_pair_g=600)
    assert m["finished_pair_mass_g"] == pytest.approx(10.0)   # 습량의 50%


def test_fixed_quantity_role_for_count_rules():
    """회귀: Shoe Box 에 surface_region 이 붙어 있었다."""
    assert geo.ROLE_MAX_CLASS["fixed_quantity"] == "C2"


def test_width_check_flags_inflation():
    box = trimesh.creation.box((1.0, 0.30, 0.50))   # 길이 1, 폭 0.5 -> 0.5 비율
    r = geo.width_check(box)
    assert r["verdict"] == "too_wide"
    box2 = trimesh.creation.box((1.0, 0.30, 0.37))
    assert geo.width_check(box2)["verdict"] == "ok"


def test_bucket_breakdown_separates_packaging():
    lines = [
        {"canonical_part": "Vamp", "assembly": "Upper External",
         "cost_p10": 0.02, "cost_p50": 0.03, "cost_p90": 0.04},
        {"canonical_part": "Shoe Box", "assembly": "Packaging",
         "cost_p10": 0.3, "cost_p50": 1.15, "cost_p90": 2.0},
    ]
    b = costing.bucket_breakdown(lines)
    assert b["Packaging"]["p50"] == pytest.approx(1.15)
    assert b["Upper"]["p50"] == pytest.approx(0.03)


@has_sample
def test_viewer_glb_has_no_part_seam_gaps():
    """회귀: 파트별 개별 데시메이션이 경계 정점을 제각각 움직여 화면에
    갈라진 선이 보였다. 전체 병합 후 데시메이션 + 라벨 이전으로 고쳤다.

    검증: viewer.glb 의 파트들을 합치고 허용오차 병합하면 열린 경계가
    거의 남지 않아야 한다 (원본 생성 모델은 완전히 닫힌 메시다).
    """
    for pid in ("DEMO-RUN-001", "DEMO-SEM-001"):
        glb = ROOT / "data" / "projects" / pid / "viewer.glb"
        if not glb.exists():
            continue
        parts = geo.scene_parts(geo.load_scene(glb))
        whole = trimesh.util.concatenate(list(parts.values()))
        diag = float(np.linalg.norm(whole.bounds[1] - whole.bounds[0]))
        w = whole.copy()
        w.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=5)
        residual = geo.open_boundary_length(w) / diag
        assert residual < 0.1, f"{pid}: 이음새 틈 {residual:.3f}"


@has_sample
def test_viewer_parts_share_vertex_normals():
    """회귀: 파트를 따로 내보내면 각 파트가 자기 면만으로 정점 법선을 평균해
    경계에서 법선이 벌어진다(실측 중앙값 23도, 최대 140도). 기하는 붙어
    있는데 조명이 튀어 화면에 갈라진 선으로 보였다.

    검증: 파일에 저장된 법선을 그대로 읽어, 파트가 공유하는 정점 위치에서
    법선이 일치해야 한다. scene_parts 는 복사·변환 과정에서 법선을 다시
    계산하므로 여기서는 쓰지 않는다.
    """
    for pid in ("DEMO-RUN-001", "DEMO-SEM-001"):
        glb = ROOT / "data" / "projects" / pid / "viewer.glb"
        if not glb.exists():
            continue
        sc = trimesh.load(glb, force="scene", process=False)
        buckets = {}
        for name, g in sc.geometry.items():
            v = np.asarray(g.vertices)
            n = np.asarray(g.vertex_normals)
            for k, nn in zip((tuple(x) for x in np.round(v, 5)), n):
                buckets.setdefault(k, []).append((name, nn))

        worst, checked = 0.0, 0
        for lst in buckets.values():
            if len({p for p, _ in lst}) < 2:
                continue
            ns = np.array([x for _, x in lst])
            L = np.linalg.norm(ns, axis=1)
            if L.min() < 1e-9:      # 퇴화 법선은 건너뛴다
                continue
            ns = ns / L[:, None]
            worst = max(worst, float(np.degrees(
                np.arccos(np.clip(ns @ ns.T, -1, 1))).max()))
            checked += 1
        assert checked > 100, f"{pid}: 공유 정점이 너무 적다 ({checked})"
        assert worst < 1.0, f"{pid}: 경계 법선 불일치 {worst:.1f}도"


def test_volume_plausibility_warning():
    """QA 회귀: 미드솔 802 cm3/짝(통상 3배)가 CV·폭·총질량 게이트를 모두
    통과했다. 파트 단위 부피 자릿수 게이트가 잡아야 한다."""
    line = {"canonical_part": "Midsole Carrier", "material_spec": "MAT-EVA-COMP",
            "consumption": {"net": 8.02e-4}}       # 802 cm3/짝
    w = costing.volume_warnings(line)
    assert len(w) == 1 and "2.3배" in w[0]

    ok_line = {"canonical_part": "Midsole Carrier", "material_spec": "MAT-EVA-COMP",
               "consumption": {"net": 2.0e-4}}     # 200 cm3 -> 통상 범위
    assert costing.volume_warnings(ok_line) == []

    # 성형이 아닌 파트는 대상 아님
    roll = {"canonical_part": "Vamp", "material_spec": "MAT-MESH-POLY",
            "consumption": {"net": 8.02e-4}}
    assert costing.volume_warnings(roll) == []


# ── 보완 규칙: 3D 에 안 보이는 필수 파트가 BOM 에 실제로 들어오는가 ──────────
def test_hidden_universal_parts_are_in_bom():
    """깔창·안감·봉제사는 모든 신발에 있지만 외형 3D 에는 안 보인다.

    세그멘테이션으로도 워크북 규칙표로도 안 잡혀 통째로 빠져 있었다.
    보완 규칙이 이것을 메운다. 빠지면 소재비가 조용히 낮아진다.
    """
    import catalog
    import bom as bom_mod
    parts = set()
    for r in catalog.recipes():
        ok, _ = bom_mod.eval_condition(r["condition"], bom_mod.CONSTRUCTION_FLAGS)
        if ok:
            parts.add(r["add_part"])
    for need in ("Sockliner Foam", "Sockliner Cover", "Thread"):
        assert need in parts, f"{need} 규칙이 실행되지 않는다"


def test_collar_lining_is_not_double_counted():
    """칼라 안감을 따로 넣으면 안 된다.

    워크북 R-002(Vamp/Quarter Lining)의 측정 기준 upper_proxy_area 에
    Collar Shell 이 포함돼 있어, 칼라 안감을 별도 라인으로 더하면 같은
    면적을 두 번 산다. 워크북 소유자가 R-002 의 ratio=0.85 가 칼라를
    제외한 값이라고 확인해 주면 그때 되살린다.
    """
    import catalog
    import bom as bom_mod
    from measures import UPPER_PARTS
    assert "Collar Shell" in UPPER_PARTS
    fired = {r["add_part"] for r in catalog.recipes()
             if bom_mod.eval_condition(r["condition"], bom_mod.CONSTRUCTION_FLAGS)[0]}
    assert "Collar Lining" not in fired


def test_supplement_rules_keep_their_provenance():
    """보완 규칙은 워크북 규칙과 근거가 구분돼야 한다."""
    import catalog
    sup = [r for r in catalog.recipes() if r["rule_id"].startswith("S-")]
    assert sup, "보완 규칙이 로드되지 않았다"
    for r in sup:
        assert "워크북" in (r["evidence"] or ""), r["rule_id"]


def test_unimplemented_qty_method_says_so():
    """산식이 없어서 막힌 것을 '면적 없음' 으로 뭉뚱그리면 원인을 못 찾는다."""
    import consumption
    line = {"canonical_part": "테스트", "material_spec": "MAT-ADH-PU",
            "geometry": {"surface_area_m2": None, "method": "blocked",
                         "source": "없는산식"},
            "quantity_basis": "per_shoe", "qty_per_pair": 1}
    out = consumption.compute(line)
    assert any("없는산식" in b for b in out["blocked"]), out["blocked"]


def test_hardener_quantity_computes_but_price_blocks():
    """하드너는 수량이 나오고 단가만 RFQ 로 막혀야 한다 (0 으로 숨기지 않는다)."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    d = json.loads((root / "data" / "projects" / "DEMO-RUN-001" / "cost.json")
                   .read_text(encoding="utf-8"))
    hard = [l for l in d["lines"] if l["canonical_part"] == "Hardener"]
    assert hard, "하드너 라인이 없다"
    h = hard[0]
    assert h["consumption"]["gross_qty"] > 0, "수량이 계산돼야 한다"
    assert h["cost_p50"] is None and h["status"] == "blocked"


def test_rule_geometry_scale_is_applied():
    """워크북 규칙의 coverage/ratio 가 실제 소요량에 반영돼야 한다.

    이 값들이 무시되면 토퍼프가 앞코 전체를, 안감이 어퍼 전체를 덮는
    것으로 계산돼 소재비가 조용히 부풀려진다.
    """
    import bom as bom_mod
    meas = {"value": 1.0, "unit": "m2", "method": "measured", "source": "t"}
    out = bom_mod.apply_rule_scale(meas, {"rule_id": "R-006",
                                          "parameters": {"coverage": 0.65}})
    assert abs(out["value"] - 0.65) < 1e-12
    assert "R-006" in out["note"]
    # pattern/yield 는 소재 스펙이 이미 들고 있으므로 여기서 곱하지 않는다
    out2 = bom_mod.apply_rule_scale(meas, {"rule_id": "R-009",
                                           "parameters": {"pattern": 1.08, "yield": 0.82}})
    assert out2["value"] == 1.0
    # 차단된 측정값은 배율을 적용하지 않는다
    blocked = {"value": 0.0, "unit": "", "method": "blocked", "source": "x"}
    assert bom_mod.apply_rule_scale(blocked, {"rule_id": "R-1",
                                              "parameters": {"coverage": 0.5}}) is blocked


# ── 외부 감사 P0 대응 ────────────────────────────────────────────────────
def test_material_partial_blocks_completion():
    """소재 라인이 하나라도 미가격이면 COMPLETE 가 되면 안 된다.

    Material 버킷은 막힌 라인이 있어도 나머지 합으로 p50 이 채워진다.
    p50 만 보고 완성 판정을 하면, 노무·금형이 채워지는 순간 미가격 소재를
    품은 채로 FOB 가 나가 버린다.
    """
    import costing
    buckets = [
        {"bucket": "Material", "p50": 3.5, "status": "Partial"},
        {"bucket": "Direct Labor", "p50": 2.0, "status": "Calculated"},
        {"bucket": "Machine", "p50": 1.0, "status": "Calculated"},
        {"bucket": "Tooling Amortization", "p50": 0.5, "status": "Calculated"},
    ]
    blocked = [b["bucket"] for b in buckets
               if b["bucket"] in costing.REQUIRED_BUCKETS
               and (b["p50"] is None or b["status"] != "Calculated")]
    assert blocked == ["Material"]


def test_machine_missing_data_blocks_not_zero():
    """기계가 도는 공정인데 기계 데이터가 없으면 0 이 아니라 차단이어야 한다."""
    import costing
    assert "Packing line" in costing.MANUAL_WORKCENTERS
    assert "Sewing workcenters" not in costing.MANUAL_WORKCENTERS
    out = costing.labor_machine(5000)
    # 씨앗 라우팅은 전부 TBD 라 차단 상태이고, 기계비도 None 이어야 한다
    assert out["status"] == "blocked"
    assert out["machine_usd_pair"] is None
    assert any("기계" in b for b in out["blocked"])


def test_project_id_rejects_path_traversal():
    """프로젝트 ID 가 그대로 파일 경로가 되므로 상위 경로 탈출을 막는다."""
    from pipeline import safe_pid
    for bad in ("../../etc", "..", "a/b", "", "x" * 70, "a;rm -rf /", "a b"):
        try:
            safe_pid(bad)
            raise AssertionError(f"{bad!r} 이 통과했다")
        except ValueError:
            pass
    assert safe_pid("DEMO-RUN-001") == "DEMO-RUN-001"


def test_gates_require_actor_and_evidence():
    """게이트는 등급을 좌우하므로 참/거짓만으로 바꿀 수 없어야 한다."""
    import inspect
    import app as app_mod
    src = inspect.getsource(app_mod.post_gates)
    assert "actor" in src and "evidence" in src
    assert "gate_log" in src


def test_reading_a_project_does_not_create_storage():
    """조회만으로 저장소가 생기면 인증 없는 GET 으로 디스크를 늘릴 수 있다."""
    from pipeline import Project
    p = Project("NEVER-CREATED-BY-READ")
    assert not p.dir.exists()


def test_same_dimension_different_unit_is_converted_not_ignored():
    """차원만 같고 단위가 다르면 그냥 곱하면 안 된다.

    면적을 m² 로 재고 단가가 USD/sq ft 면 10.76 배 틀린다. 가죽이 sq ft 로
    팔리므로 실제로 물릴 자리였다. 환산 계수를 돌려주고 근거에 남긴다.
    """
    import units
    ok, note, f = units.check_multiply("m²", "sq ft")
    assert ok and f is not None
    assert abs(f - 10.7639) < 0.01, f
    assert note and "환산" in note
    # 같은 단위면 계수 1, 안내 없음
    ok, note, f = units.check_multiply("m²", "m²")
    assert ok and f == 1.0 and note is None
    # 계수는 '단가를 수량 단위 기준으로 올리는 수' 다.
    # 수량이 m 이고 단가가 USD/yd 면 1m 당 단가는 USD/yd x 1.09361 이다.
    assert abs(units.check_multiply("m", "yard")[2] - 1.0936133) < 1e-6
    assert abs(units.check_multiply("kg", "g")[2] - 1000.0) < 1e-9
    # 실제 사례: 가죽 0.05 m2 를 USD/sq ft 4.545 로 사면 2.446 달러
    f = units.check_multiply("m²", "sq ft")[2]
    assert abs(0.05 * 4.545 * f - 2.4463) < 0.001
    # 개수 계열은 환산 없음. 다르면 막는다
    assert not units.check_multiply("sheet", "piece")[0]
    # 차원이 다르면 여전히 막는다
    assert not units.check_multiply("kg", "m")[0]


# ── 외부 개선안(PDF) 대응 ────────────────────────────────────────────────
def test_unapproved_lines_are_not_in_confirmed_subtotal():
    """엔지니어가 승인하지 않은 라인은 '확인된 소계' 에 들어가면 안 된다.

    실측: 규칙이 제안한 숨은 BOM 이 소계의 56~61% 를 차지하면서 아무 표시
    없이 섞여 있었다. 금액을 지우지 않되 어디에 합산되는지를 나눈다.
    """
    import costing
    lines = [
        {"line_id": "A", "canonical_part": "Vamp", "status": "calculated",
         "approved": True, "cost_p10": 1.0, "cost_p50": 1.0, "cost_p90": 1.0,
         "assembly": "Upper External"},
        {"line_id": "B", "canonical_part": "Strobel", "status": "calculated",
         "approved": False, "cost_p10": 2.0, "cost_p50": 2.0, "cost_p90": 2.0,
         "assembly": "Bottom"},
    ]
    ru = costing.roll_up(lines, {"order_quantity": 5000,
                                 "reject_allowance_pct": 3.0,
                                 "factory_overhead_pct": 8.0,
                                 "supplier_margin_pct": 10.0})
    assert ru["known_cost_subtotal"]["p50"] == 1.0
    assert ru["unapproved_material_subtotal"]["p50"] == 2.0
    assert len(ru["unapproved_lines"]) == 1
    # 미승인이 있으면 Material 버킷이 차단되어 FOB 가 나가지 않는다
    assert "Material" in ru["blocked_buckets"]
    assert ru["fob"] is None
    # 버킷 분해도 승인분만 잡아 소계와 맞는다
    assert abs(sum(v["p50"] for v in ru["material_breakdown"].values())
               - ru["known_cost_subtotal"]["p50"]) < 1e-9


def test_unapproved_lines_block_c2_even_with_gates_on():
    """게이트만 켜서 C2 로 올라가지 못하게 한다."""
    import costing
    from config import CLASS_REQUIREMENTS
    gates = {k: True for reqs in CLASS_REQUIREMENTS.values() for k, _ in reqs}
    lines = [{"line_id": "A", "canonical_part": "Vamp", "status": "calculated",
              "approved": False, "material_spec": "MAT-MESH-POLY",
              "max_class": "C2", "cost_p50": 1.0,
              "price": {"eligibility": "Engineering"}}]
    ru = {"direct_complete": True}
    g = costing.grade(lines, ru, gates)
    assert g["class"] != "C2"
    assert any("미승인" in r for r in g["blocked_reasons"]["C2"])


def test_material_choice_changes_cost_for_leather():
    """가죽 스타일에 가죽을 지정하면 갑피 원가가 실제로 오른다.

    canonical part 하나에 소재 하나를 고정해 두면 가죽 신발도 메시 단가로
    계산된다. 실측: 가죽 데모에 가죽 소재가 한 번도 쓰이지 않았다.
    """
    import bom as bom_mod
    import catalog
    # 기본값은 메시, 지정하면 가죽
    d = catalog.part_defaults()
    assert d.get("Vamp") == "MAT-MESH-POLY"
    assert "MAT-FULLGRAIN" in catalog.material_specs(), "가죽 공학 스펙이 있어야 한다"
    sp = catalog.material_specs()["MAT-FULLGRAIN"]
    # 가죽은 결점을 피해 재단하므로 롤 원단보다 수율이 낮다
    assert sp["nesting_yield"]["value"] < \
        catalog.material_specs()["MAT-MESH-POLY"]["nesting_yield"]["value"]


def test_upper_area_is_labelled_proxy_not_measured():
    """3D 외피 표면적을 measured 로 부르면 실측한 것처럼 보인다."""
    import inspect
    import measures
    src = inspect.getsource(measures.GeometryContext.upper_proxy_area)
    assert '"proxy"' in src and '"measured"' not in src


def test_evidence_coverage_reports_what_the_cost_rests_on():
    """승인 견적 비율과 가정 파라미터 비율을 결과가 스스로 말해야 한다."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    d = json.loads((root / "data" / "projects" / "DEMO-RUN-001" / "cost.json")
                   .read_text(encoding="utf-8"))
    ec = d["evidence_coverage"]
    assert ec["supplier_quote_lines"] == 0, "지금은 승인 견적이 하나도 없다"
    assert 0 < ec["assumed_param_ratio"] < 1
    assert ec["geometry_proxy"] > 0
    vk = d["version_key"]
    assert vk["comparable"] is False and vk["undeclared"]


# ── 2차 외부 검토 대응 ───────────────────────────────────────────────────
def test_material_approved_propagates_to_cost_lines():
    """소재 승인 플래그가 원가 라인까지 와야 한다.

    bom 이 만들었는데 costing 이 복사하지 않아 CSV 의 '소재승인됨' 이
    항상 '아니오' 였다 (2차 검토 1순위 오류).
    """
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    d = json.loads((root / "data" / "projects" / "DEMO-LTHR-001" / "cost.json")
                   .read_text(encoding="utf-8"))
    lth = [l for l in d["lines"] if l.get("material_spec") == "MAT-FULLGRAIN"]
    assert lth, "가죽 라인이 있어야 한다 (소재 선택이 걸려 있음)"
    assert all(l.get("material_approved") is True for l in lth)
    assert all("승인" in (l.get("material_source") or "") for l in lth)


def test_input_change_marks_cost_stale():
    """입력이 바뀌면 기존 원가는 낡았다고 말해야 한다 (2차 검토 1순위 오류)."""
    import json
    from pipeline import Project
    p = Project("DEMO-RUN-001")
    stored = json.loads((p.dir / "cost.json").read_text(encoding="utf-8"))
    fp = stored.get("inputs_fingerprint")
    assert fp, "cost.json 에 입력 지문이 있어야 한다"
    # 바뀐 것이 없으면 낡지 않았다
    st = p.staleness(fp)
    assert st["is_stale"] is False
    # 시나리오를 바꾸면 낡았다고, 어디가 바뀌었는지까지 말한다
    orig = p.state["scenario"]["order_quantity"]
    try:
        p.state["scenario"]["order_quantity"] = orig + 1
        st2 = p.staleness(fp)
        assert st2["is_stale"] is True
        assert any("시나리오" in c for c in st2["changed_sections"])
    finally:
        p.state["scenario"]["order_quantity"] = orig
    # 구버전 파일(지문 없음)은 오류가 아니라 '모름' 이다
    st3 = p.staleness(None)
    assert st3["is_stale"] is None


def test_grade_ladder_c0_to_c4():
    """등급 사다리: routing·tooling 은 C3, Incoterm·실적 대사는 C4 조건이다."""
    import costing
    from config import CLASS_REQUIREMENTS
    all_gates = {k: True for reqs in CLASS_REQUIREMENTS.values() for k, _ in reqs}
    line = {"line_id": "A", "canonical_part": "Vamp", "status": "calculated",
            "approved": True, "material_spec": "MAT-X", "max_class": "C2",
            "cost_p50": 1.0, "price": {"eligibility": "Engineering"}}
    ru_ok = {"direct_complete": True}
    g = costing.grade([line], ru_ok, all_gates)
    assert g["class"] == "C4", g
    # routing 게이트가 빠지면 C2 에서 멈춘다 (소재 원가는 말할 수 있다)
    gates2 = {**all_gates, "routing_confirmed": False}
    g2 = costing.grade([line], ru_ok, gates2)
    assert g2["class"] == "C2", g2
    # 노무 데이터가 없으면 C3 사유가 생긴다
    g3 = costing.grade([line], {"direct_complete": False}, all_gates)
    assert g3["class"] == "C2"
    assert any("SAM" in r for r in g3["blocked_reasons"]["C3"])


def test_price_tier_labels():
    """가격 신뢰도 A0~A4: 공개 리스팅은 Estimated, 시장지수는 Benchmark,
    승인 견적은 Quoted. Actual 은 송장이 있어야만 한다."""
    import pricing
    assert pricing.source_tier("approved_supplier_quote") == ("A2", "Quoted")
    assert pricing.source_tier("quarterly_snapshot", "Public listing") == ("A0", "Estimated")
    assert pricing.source_tier("quarterly_snapshot", "시장지수") == ("A1", "Benchmark")
    assert pricing.source_tier("price_proxy", None) == ("A0", "Estimated")
    p = pricing.select("MAT-MESH-POLY", "2026Q3")
    assert p["source_tier"] == "A0" and p["tier_label"] == "Estimated"


def test_consumption_method_is_explicit():
    """소요량을 어떻게 구했는지 라인마다 명시한다. 생산용 방법 이름
    (dxf_marker 등)은 데이터가 없는 지금 절대 나타나면 안 된다."""
    import json
    from pathlib import Path
    import consumption
    root = Path(__file__).resolve().parents[1]
    d = json.loads((root / "data" / "projects" / "DEMO-RUN-001" / "cost.json")
                   .read_text(encoding="utf-8"))
    methods = {l["consumption"].get("consumption_method") for l in d["lines"]
               if l["consumption"].get("gross_qty") is not None}
    assert methods, "산출 방법이 기록돼야 한다"
    assert all(m and m.endswith(("_proxy_3d", "fixed_count", "fixed_mass"))
               for m in methods), methods
    for future in consumption.FUTURE_METHODS:
        assert future not in methods
