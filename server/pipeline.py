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
            scene.add_geometry(_decimate(m, budget), geom_name=name, node_name=name)
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
        self.state["calibration"] = cal
        self.state["landmarks"] = {**lm, "toe": toe, "heel": heel,
                                   "confirmed": bool(confirmed)}
        self.state["gates"]["metric_calibrated"] = bool(confirmed)
        self._mark("scale", "confirmed" if confirmed else "needs_review",
                   scale=cal["scale"])
        return cal

    # ── 4) Segment -> Canonical ──────────────────────────────────────
    def propose_mapping(self):
        lm = self.state.get("landmarks")
        parts = self._parts()
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
            r = rp.repair_to_solid(parts[sid])
            if r["ok"]:
                vol_m3 = geo.to_si(r["raw_volume"], "volume", cal)
                out[sid] = {
                    "ok": True, "canonical_part": cp, "method": r["method"],
                    "volume_m3": vol_m3, "volume_cm3": vol_m3 * 1e6,
                    "note": r["note"], "confidence_penalty": r["confidence_penalty"],
                }
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
        result = {"scenario": sc, "lines": cl, "rollup": rollup, "grade": gr,
                  "computed_at": _now()}
        self.state["cost"] = {"rollup": rollup, "grade": gr,
                              "computed_at": result["computed_at"]}
        (self.dir / "cost.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        self._mark("cost", "calculated", grade=gr["class"],
                   fob=rollup["fob_status"])
        return result


def import_tripo_outputs(pid, raw_glb=None, segmented_glb=None,
                         generate_task=None, segment_task=None, image=None):
    """이미 만들어둔 Tripo 산출물을 프로젝트로 들인다 (재과금 없이 검증용)."""
    p = Project(pid)
    for src, name in ((raw_glb, "raw_model.glb"), (segmented_glb, "segmented.glb"),
                      (generate_task, "tripo_generate_task.json"),
                      (segment_task, "tripo_segment_task.json")):
        if src and Path(src).resolve() != (p.dir / name).resolve():
            shutil.copy2(src, p.dir / name)
    if image:
        dst = ASSETS / f"{pid}{Path(image).suffix.lower()}"
        if Path(image).resolve() != dst.resolve():
            shutil.copy2(image, dst)
        p.state["input_image"] = dst.name
    p._mark("generate3d", "imported")
    return p
