# -*- coding: utf-8 -*-
"""프로젝트 상태와 파이프라인 단계 실행.

단계는 계획서 §17의 워크플로 그대로다.
    Design -> 3D -> Scale -> Segment -> Manufacturing Definition
    -> BOM -> Consumption -> Routing -> Pricing -> Cost Approval

각 단계는 앞 단계의 산출물을 파일로 남겨 다시 계산할 수 있게 한다.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh

import bom as bom_mod
import canonical
import costing
import geometry as geo
import measures
from config import STORE, ASSETS


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mapping_summary(mapping):
    """매핑 지표를 정직하게 센다.

    "8/8 일치" 는 정확도가 아니다. 정답 데이터가 없으면 그건 후보를 전부
    어떤 class 에 배정했다는 배정 커버리지일 뿐이다.
    """
    auto = [m for m in mapping if m.get("status") == "ai_proposed"]
    review = [m for m in mapping if m.get("status") == "needs_review"]
    confirmed = [m for m in mapping if m.get("confirmed")]
    assigned = sum(1 for m in mapping if m.get("canonical_part"))
    return {
        "input_segments": len(mapping),
        "assigned_segments": assigned,
        "auto_accepted": len(auto),
        "needs_review": len(review),
        "engineer_confirmed": len(confirmed),
        "assignment_coverage": (assigned / len(mapping)) if mapping else 0.0,
        "review_rate": (len(review) / len(mapping)) if mapping else 0.0,
        "note": "정답 데이터가 없으므로 정확도가 아니라 배정 커버리지다",
    }


def _decimate(mesh, budget):
    if mesh.faces.shape[0] <= budget:
        return mesh
    try:
        import fast_simplification as fs
        v, f = fs.simplify(np.asarray(mesh.vertices, dtype=np.float32),
                           np.asarray(mesh.faces, dtype=np.int32),
                           target_count=int(budget))
        return trimesh.Trimesh(vertices=v, faces=f, process=False)
    except Exception:
        idx = np.random.default_rng(0).choice(mesh.faces.shape[0], int(budget), replace=False)
        return mesh.submesh([idx], append=True)


class Project:
    def __init__(self, pid):
        self.pid = pid
        self.dir = STORE / pid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = self._load()

    # ── 상태 ─────────────────────────────────────────────────────────
    def _load(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "project_id": self.pid,
            "created_at": _now(),
            "steps": {},
            "scenario": {
                "style_id": self.pid,
                "construction": "Strobel Cemented",
                "reference_size_label": 260,
                "order_quantity": 5000,
                "currency": "USD",
                "quarter": "2026Q3",
                "reject_allowance_pct": 3.0,
                "factory_overhead_pct": 8.0,
                "supplier_margin_pct": 10.0,
                # 질량 정합성 검사용. 실제 샘플 무게를 넣으면 정확해진다.
                "target_pair_weight_g": 600,
            },
            "gates": {},
        }

    def save(self):
        self.state["updated_at"] = _now()
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    def _mark(self, step, status, **extra):
        self.state["steps"][step] = {"status": status, "at": _now(), **extra}
        self.save()

    # ── 캐시된 메시 ───────────────────────────────────────────────────
    def _parts(self):
        glb = self.dir / "segmented.glb"
        if not glb.exists():
            raise FileNotFoundError("segmented.glb 가 없습니다. 세그멘테이션을 먼저 실행하세요.")
        if not hasattr(self, "_parts_cache"):
            sc = geo.load_scene(glb)
            self._parts_cache = geo.scene_parts(sc)
        return self._parts_cache

    def _whole(self):
        return trimesh.util.concatenate(list(self._parts().values()))

    def _completed_parts(self):
        """메시 완성 결과. 세그먼트와 인덱스가 1대1 로 대응한다.

        완성본은 안팎 양면을 가진 닫힌 껍질이라 복셀 부피가 해상도에 훨씬
        덜 민감하다. 실측으로 CV 28% 에서 2에서 10% 로 떨어졌다.
        """
        glb = self.dir / "completed.glb"
        if not glb.exists():
            return {}
        if not hasattr(self, "_completed_cache"):
            done = geo.scene_parts(geo.load_scene(glb))
            segs = self._parts()
            # 이름 접두사는 생성기마다 다르므로 끝자리 인덱스로 맞춘다.
            by_idx = {n.rsplit("_", 1)[-1]: m for n, m in done.items()}
            out = {}
            for sid in segs:
                m = by_idx.get(sid.rsplit("_", 1)[-1])
                if m is not None:
                    out[sid] = m
            self._completed_cache = out
        return self._completed_cache

    # ── 뷰어용 경량 GLB ───────────────────────────────────────────────
    def viewer_glb(self, face_budget=160_000, force=False):
        """브라우저용 경량 GLB. 파트 이름과 배치를 그대로 보존한다.

        원본은 40MB가 넘어 그대로 내려보내면 뷰어가 버틴다 해도 느리다.
        파트별 면적 비중에 비례해 예산을 나눠 형상을 고르게 남긴다.
        """
        out = self.dir / "viewer.glb"
        if out.exists() and not force:
            return out
        parts = self._parts()
        total_faces = sum(m.faces.shape[0] for m in parts.values())
        scene = trimesh.Scene()
        for name, m in parts.items():
            if name.endswith("__repaired"):
                continue
            share = m.faces.shape[0] / max(total_faces, 1)
            budget = max(600, int(face_budget * share))
            d = _decimate(m, budget)
            # 법선을 실어 보내지 않으면 브라우저에서 조명이 먹지 않아 검게 나온다.
            d.vertex_normals
            scene.add_geometry(d, geom_name=name, node_name=name)
        scene.export(out)
        return out

    # ── 3) Scale ─────────────────────────────────────────────────────
    def propose_landmarks(self):
        lm = geo.long_axis_landmarks(self._whole())
        self.state["landmarks"] = lm
        self._mark("scale", "ai_proposed", raw_length=lm["raw_length"])
        return lm

    def calibrate(self, target_length_mm, toe=None, heel=None, confirmed=False):
        lm = self.state.get("landmarks") or self.propose_landmarks()
        toe = toe or lm["toe"]
        heel = heel or lm["heel"]
        cal = geo.calibration(target_length_mm, toe, heel)
        cal["confirmed"] = bool(confirmed)
        # 측면 한 장 복원은 폭 정보가 없다. 폭/길이 비율이 통상 범위를
        # 벗어나면 경고하고 부피 파트 신뢰를 낮춘다. 자동 축소는 하지 않는다.
        try:
            cal["width_check"] = geo.width_check(self._whole())
        except Exception:
            pass
        self.state["calibration"] = cal
        self.state["landmarks"] = {**lm, "toe": toe, "heel": heel,
                                   "confirmed": bool(confirmed)}
        self.state["gates"]["metric_calibrated"] = bool(confirmed)
        self._mark("scale", "confirmed" if confirmed else "needs_review",
                   scale=cal["scale"])
        return cal

    # ── 4) Segment -> Canonical ──────────────────────────────────────
    def _model_mapping(self):
        """사내 세그멘테이션 모델의 매핑이 있으면 그것을 쓴다.

        기하 추정은 라벨이 없을 때의 폴백이다. 모델은 클래스명과 신뢰도를
        직접 주므로 배정이 훨씬 안정적이다 (신발 도메인 mAP 0.47).
        """
        f = self.dir / "model_mapping.json"
        if not f.exists():
            return None
        d = json.loads(f.read_text(encoding="utf-8"))
        parts = self._parts()
        total_area = sum(m.area for m in parts.values()) or 1.0
        out = []
        for item in d["mapping"]:
            mesh = parts.get(item["segment_id"])
            if mesh is None:
                continue
            out.append({
                **item,
                "score": item["confidence"],
                "margin": None,
                "alternatives": [],
                "features": {"area_share": float(mesh.area) / total_area},
            })
        self.state["segmentation_source"] = {
            "kind": "internal_model",
            "face_coverage": d.get("face_coverage"),
            "display_parts_merged": d.get("display_parts"),
        }
        return out

    def propose_mapping(self):
        lm = self.state.get("landmarks")
        parts = self._parts()
        m = self._model_mapping()
        if m is None:
            m = canonical.propose(parts,
                                  toe=lm.get("toe") if lm else None,
                                  heel=lm.get("heel") if lm else None)
        # 파트별 QA 를 함께 실어 UI 가 부피 가능 여부를 바로 보여줄 수 있게 한다.
        cal = self.state.get("calibration")
        for item in m:
            mesh = parts[item["segment_id"]]
            item["qa"] = geo.mesh_qa(mesh)
            if cal:
                item["metrics"] = geo.part_metrics(mesh, cal, item["canonical_part"])
        self.state["mapping"] = m
        self.state["gates"]["segmented"] = True
        self.state["mapping_summary"] = mapping_summary(m)
        self._mark("segment", "ai_proposed", segments=len(m))
        return m

    def confirm_mapping(self, overrides=None, confirm_all=False):
        """사용자가 매핑을 고치거나 확정한다."""
        m = self.state.get("mapping") or self.propose_mapping()
        ov = overrides or {}
        for item in m:
            if item["segment_id"] in ov:
                item["canonical_part"] = ov[item["segment_id"]]
                item["status"] = "user_assigned"
                item["confirmed"] = True
            elif confirm_all:
                item["status"] = "engineer_confirmed"
                item["confirmed"] = True
        self.state["mapping"] = m
        self.state["mapping_summary"] = mapping_summary(m)
        done = all(i.get("confirmed") for i in m)
        self.state["gates"]["construction_set"] = True
        self._mark("segment", "confirmed" if done else "needs_review",
                   confirmed=sum(1 for i in m if i.get("confirmed")), total=len(m))
        return m

    # ── 4b) Mesh repair (선택) ───────────────────────────────────────
    def repair_volumes(self, segment_ids=None):
        """부피가 막힌 솔리드 파트를 복구한다 (계획서 §5.5 fallback).

        복구본은 원본을 덮어쓰지 않고 따로 보관하며, 어떤 방법으로 닫았는지와
        신뢰도 감점을 함께 남긴다.
        """
        import repair as rp

        cal = self.state.get("calibration")
        if not cal:
            raise ValueError("캘리브레이션이 없습니다.")
        parts = self._parts()
        mapping = self.state.get("mapping") or self.propose_mapping()
        out = {}
        for item in mapping:
            cp, sid = item["canonical_part"], item["segment_id"]
            if cp not in geo.VOLUME_ALLOWED_PARTS:
                continue
            if segment_ids and sid not in segment_ids:
                continue
            if item.get("qa", {}).get("is_volume"):
                continue                      # 이미 닫혀 있으면 건드리지 않는다
            completed = self._completed_parts().get(sid)
            if completed is not None:
                # 완성본을 우선 쓴다. 복셀로 임의로 채운 것이 아니라 모델이
                # 내부 면을 만들어 낸 것이라 해상도에 훨씬 덜 민감하다.
                sens = rp.volume_sensitivity(completed, scale_mm_per_unit=cal["scale"])
                if sens.get("mean"):
                    vol_m3 = geo.to_si(sens["mean"], "volume", cal)
                    out[sid] = {
                        "ok": True, "canonical_part": cp, "tier": "R3",
                        "method": "메시 완성 후 복셀 3해상도 평균",
                        "geometry_role": "repaired_volume_proxy",
                        "max_class": geo.ROLE_MAX_CLASS["repaired_volume_proxy"],
                        "volume_m3": vol_m3, "volume_cm3": vol_m3 * 1e6,
                        "sensitivity": sens,
                        "usable": sens["verdict"] != "blocked",
                        "note": ("파트를 닫은 뒤 부피를 잰 값이다. 실제 제조 내부"
                                 " 구조를 보장하지 않으므로 C1 proxy 로만 쓴다."),
                        "confidence_penalty": 2,
                    }
                    continue

            r = rp.repair_to_solid(parts[sid])
            if r["ok"]:
                vol_m3 = geo.to_si(r["raw_volume"], "volume", cal)
                sens = rp.volume_sensitivity(parts[sid], scale_mm_per_unit=cal["scale"])
                out[sid] = {
                    "ok": True, "canonical_part": cp, "tier": "R4",
                    "method": r["method"],
                    "geometry_role": "repaired_volume_proxy",
                    "max_class": geo.ROLE_MAX_CLASS["repaired_volume_proxy"],
                    "volume_m3": vol_m3, "volume_cm3": vol_m3 * 1e6,
                    "sensitivity": sens,
                    "usable": sens["verdict"] != "blocked",
                    "note": r["note"], "confidence_penalty": r["confidence_penalty"],
                }
                if sens["verdict"] != "blocked":
                    self._parts_cache[sid + "__repaired"] = r["mesh"]
            else:
                out[sid] = {"ok": False, "canonical_part": cp, "note": r["note"],
                            "steps": r["steps"][-3:]}
        self.state["repairs"] = out
        ok_n = sum(1 for v in out.values() if v.get("ok"))
        self._mark("repair", "repaired" if ok_n else "failed",
                   repaired=ok_n, attempted=len(out))
        return out

    # ── 5) BOM ───────────────────────────────────────────────────────
    def _context(self):
        cal = self.state.get("calibration")
        if not cal:
            raise ValueError("캘리브레이션이 없습니다. /calibrate 를 먼저 호출하세요.")
        parts = self._parts()
        frame = canonical.canonical_frame(
            self._whole(),
            toe=(self.state.get("landmarks") or {}).get("toe"),
            heel=(self.state.get("landmarks") or {}).get("heel"))
        return parts, cal, frame

    def build_bom(self, flags=None):
        parts, cal, frame = self._context()
        mapping = self.state.get("mapping") or self.propose_mapping()
        ctx = measures.GeometryContext(parts, mapping, cal, frame)
        lines = bom_mod.build(mapping, ctx, cal, parts,
                              flags=flags,
                              construction=self.state["scenario"]["construction"],
                              repairs=self.state.get("repairs"))
        self.state["bom"] = lines
        self.state["gates"]["mbom_built"] = True
        # 부피가 필요한 라인이 전부 QA를 통과했는지
        vol_lines = [l for l in lines
                     if (l.get("formula_family") or "").startswith("molded")]
        self.state["gates"]["volume_parts_validated"] = bool(vol_lines) and all(
            l["geometry"].get("volume_m3") is not None for l in vol_lines)
        self._mark("bom", "built", lines=len(lines),
                   hidden=sum(1 for l in lines if l["origin"] == "construction_rule"))
        return lines

    # ── 6-9) Cost ────────────────────────────────────────────────────
    def estimate(self):
        lines = self.state.get("bom") or self.build_bom()
        sc = self.state["scenario"]
        cl = costing.cost_lines(lines, sc["quarter"],
                                self.state.get("supplier_quotes"))
        rollup = costing.roll_up(cl, sc)
        gr = costing.grade(cl, rollup, self.state.get("gates", {}))
        mass = costing.mass_balance(cl, sc.get("target_pair_weight_g"))
        result = {"scenario": sc, "lines": cl, "rollup": rollup, "grade": gr,
                  "mass_balance": mass, "computed_at": _now()}
        self.state["cost"] = {"rollup": rollup, "grade": gr,
                              "computed_at": result["computed_at"]}
        (self.dir / "cost.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        self._mark("cost", "calculated", grade=gr["class"],
                   fob=rollup["fob_status"])
        return result


def import_mesh_provider_outputs(pid, raw_glb=None, segmented_glb=None,
                         generate_task=None, segment_task=None, image=None):
    """이미 만들어둔 3D 생성 엔진 산출물을 프로젝트로 들인다 (재과금 없이 검증용)."""
    p = Project(pid)
    for src, name in ((raw_glb, "raw_model.glb"), (segmented_glb, "segmented.glb"),
                      (generate_task, "generate_task.json"),
                      (segment_task, "segment_task.json")):
        if src and Path(src).resolve() != (p.dir / name).resolve():
            shutil.copy2(src, p.dir / name)
    if image:
        dst = ASSETS / f"{pid}{Path(image).suffix.lower()}"
        if Path(image).resolve() != dst.resolve():
            shutil.copy2(image, dst)
        p.state["input_image"] = dst.name
    p._mark("generate3d", "imported")
    return p
