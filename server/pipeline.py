# -*- coding: utf-8 -*-
"""프로젝트 상태와 파이프라인 단계 실행.

단계는 계획서 §17의 워크플로 그대로다.
    Design -> 3D -> Scale -> Segment -> Manufacturing Definition
    -> BOM -> Consumption -> Routing -> Pricing -> Cost Approval

각 단계는 앞 단계의 산출물을 파일로 남겨 다시 계산할 수 있게 한다.
"""
import json
import re as _re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh

import bom as bom_mod
import canonical
import catalog
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


# 프로젝트 ID 는 파일 경로가 된다. 임의 문자열을 그대로 붙이면 상위 경로로
# 빠져나갈 수 있고, mkdir(parents=True) 라 디렉터리까지 만들어진다.
PID_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def safe_pid(pid):
    """허용된 모양의 프로젝트 ID 만 통과시킨다."""
    pid = (pid or "").strip()
    if not PID_RE.match(pid):
        raise ValueError(
            "프로젝트 ID 는 영문·숫자로 시작하고 영문·숫자·하이픈·밑줄만 "
            "쓸 수 있습니다 (최대 64자).")
    return pid


class Project:
    def __init__(self, pid):
        self.pid = safe_pid(pid)
        pid = self.pid
        self.dir = STORE / pid
        # 조회만 해도 디렉터리가 생기면, 인증 없는 GET 으로 저장소를 늘릴 수
        # 있다. 실제로 쓸 때만 만든다.
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

    def ensure_dir(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def save(self):
        self.ensure_dir()
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
            # 이미 세그멘테이션을 돌린 프로젝트인데 파일만 없는 경우가 있다
            # (원본 메시를 뺀 배포본). 둘을 구분해 줘야 사용자가 헛수고를 안 한다.
            if (self.state.get("steps") or {}).get("segment3d"):
                raise FileNotFoundError(
                    "이 서버에는 원본 파트 메시가 없어 다시 계산할 수 없습니다. "
                    "미리 계산된 결과는 볼 수 있습니다.")
            raise FileNotFoundError(
                "파트 메시가 없습니다. 세그멘테이션을 먼저 실행하세요.")
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

        파트별로 따로 데시메이션하면 인접 파트의 경계 정점이 제각각 움직여
        실제 기하 틈(화면의 갈라진 선)이 생긴다. 그래서 전체 메시를 한 번에
        데시메이션한 뒤, 원본 face 최근접으로 파트 라벨을 이전해 다시 쪼갠다.
        경계 양쪽이 같은 데시메이션 정점을 공유하므로 틈이 없다.
        """
        out = self.dir / "viewer.glb"
        if out.exists() and not force:
            return out
        self.ensure_dir()
        parts = self._parts()
        names = list(parts.keys())

        # 원본 face -> 파트 라벨 (concatenate 는 face 순서를 보존한다)
        import numpy as _np
        whole = trimesh.util.concatenate([parts[n] for n in names])
        labels = _np.concatenate([
            _np.full(parts[n].faces.shape[0], i, dtype=_np.int32)
            for i, n in enumerate(names)])

        # 파트 이음새의 좌표 일치 정점을 먼저 병합한다. 안 하면 데시메이터가
        # 이음새를 열린 경계로 보고 양쪽을 제각각 움직여 틈을 새로 만든다.
        # 세그멘테이션 전 원본은 완전히 닫힌 메시다(열린경계 0). 지금 보이는
        # 틈은 전부 세그멘테이션이 만든 1e-8 수준의 정점 불일치이므로,
        # 허용오차 병합(소수 5자리)으로 이음새를 닫은 뒤 데시메이션한다.
        merged = whole.copy()
        merged.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=5)

        dec = _decimate(merged, face_budget)
        if dec is merged:
            dec = merged.copy()

        # 데시메이션 face 중심 -> 최근접 원본 face 중심의 라벨
        from scipy.spatial import cKDTree
        tree = cKDTree(whole.triangles_center)
        _d, idx = tree.query(dec.triangles_center, k=1, workers=-1)
        dec_labels = labels[idx]

        # 파트 경계의 계단 모양을 다듬는다. 이웃 face 다수결을 몇 번 돌리면
        # 지그재그가 줄어 화면이 깔끔해진다. 표시용 경계만 손대며,
        # 원가에 쓰는 면적은 원본 세그멘테이션 그대로다.
        try:
            adj = dec.face_adjacency
            for _ in range(3):
                nb = {}
                for a, b in adj:
                    nb.setdefault(a, []).append(b)
                    nb.setdefault(b, []).append(a)
                new_labels = dec_labels.copy()
                for fi, ns in nb.items():
                    vals, cnt = _np.unique(dec_labels[ns], return_counts=True)
                    top = vals[int(_np.argmax(cnt))]
                    # 이웃 다수가 다른 라벨이고 그 수가 2 이상일 때만 바꾼다
                    if top != dec_labels[fi] and cnt.max() >= 2:
                        new_labels[fi] = top
                if (new_labels == dec_labels).all():
                    break
                dec_labels = new_labels
        except Exception:
            pass

        # 법선은 전체 메시에서 한 번만 계산해 파트들이 공유한다.
        # 파트마다 따로 계산하면 경계에서 각자 자기 면만 평균해 법선이
        # 벌어지고(실측 중앙값 23도, 최대 140도) 조명이 튀어 갈라진 선으로
        # 보인다. 기하는 붙어 있으므로 이건 구멍이 아니라 셰이딩 문제다.
        dec_normals = _np.asarray(dec.vertex_normals)

        scene = trimesh.Scene()
        for i, name in enumerate(names):
            fidx = _np.where(dec_labels == i)[0]
            if fidx.size == 0:
                continue
            faces = dec.faces[fidx]
            uniq, inv = _np.unique(faces, return_inverse=True)
            sub = trimesh.Trimesh(
                vertices=_np.asarray(dec.vertices)[uniq],
                faces=inv.reshape(-1, 3),
                vertex_normals=dec_normals[uniq],
                process=False,          # 정점을 다시 손대면 법선 대응이 깨진다
            )
            scene.add_geometry(sub, geom_name=name, node_name=name)
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
    def ensure_completed(self):
        """메시 완성본(completed.glb)이 없으면 생성 엔진으로 만든다.

        R4 복셀 리메시는 작은 파트에서 해상도 민감도가 커 부피가 자주 차단된다.
        완성본이 있으면 R3 경로(민감도 실측 CV 2~10%)를 탈 수 있다. 크레딧이
        들므로 세그멘테이션 task id 가 있을 때만, 한 번만 시도한다.
        """
        glb = self.dir / "completed.glb"
        if glb.exists():
            return True
        tid = ((self.state.get("steps") or {}).get("segment3d") or {}).get("task_id")
        if not tid:
            return False
        from mesh_provider import MeshProvider
        c = MeshProvider()
        ctid = c.mesh_complete(tid)
        data = c.wait(ctid)
        c.download_model(data, glb)
        self._mark("mesh_complete", "done", task_id=ctid)
        return True

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

    def set_materials(self, choices):
        """파트 또는 세그먼트별 소재를 승인한다.

        canonical part 하나에 소재 하나를 고정하면 가죽 신발도 메시 단가로
        계산된다. 승인된 선택만 '사용자 승인' 으로 표시하고, 나머지는
        기본값이라고 밝혀 등급 상한을 걸어 둔다.
        """
        specs = catalog.material_specs()
        prices = catalog.quarterly_prices()
        q = self.state["scenario"]["quarter"]
        cur = dict(self.state.get("materials") or {})
        log = self.state.setdefault("material_log", [])
        for key, spec in (choices or {}).items():
            if spec is None:
                cur.pop(key, None)
                log.append({"target": key, "material_spec": None, "at": _now()})
                continue
            if spec not in specs:
                raise ValueError(f"알 수 없는 소재: {spec}")
            if (q, spec) not in prices and not specs[spec].get("price_proxy"):
                raise ValueError(f"{spec} 는 {q} 분기 단가가 없어 선택할 수 없습니다")
            before = cur.get(key)
            cur[key] = spec
            log.append({"target": key, "from": before, "material_spec": spec,
                        "at": _now()})
        self.state["materials"] = cur
        self._mark("materials", "confirmed", selected=len(cur))
        return cur

    def inputs_fingerprint(self):
        """원가에 들어가는 입력의 단면 지문.

        시나리오·소재 선택·게이트·매핑·복구 중 하나라도 바뀌면 기존
        cost.json 은 낡은 것이다. 외부 검토가 이것을 1순위 오류로 지적했다:
        입력을 바꿔도 이전 결과가 아무 표시 없이 그대로 보였다.
        """
        import hashlib

        def h(obj):
            return hashlib.sha256(
                json.dumps(obj, sort_keys=True, ensure_ascii=False,
                           default=str).encode()).hexdigest()[:16]

        st = self.state
        mapping = [(m.get("segment_id"), m.get("canonical_part"),
                    bool(m.get("confirmed")))
                   for m in (st.get("mapping") or [])]
        repairs = {k: (v.get("volume_m3"), v.get("usable"))
                   for k, v in (st.get("repairs") or {}).items()}
        approvals = [(l.get("line_id"), l.get("approval_status"))
                     for l in (st.get("bom") or [])]
        return {
            "scenario": h(st.get("scenario") or {}),
            "materials": h(st.get("materials") or {}),
            "gates": h(st.get("gates") or {}),
            "mapping": h(mapping),
            "repairs": h(repairs),
            "bom_approvals": h(approvals),
        }

    def staleness(self, stored):
        """저장된 원가의 입력 지문과 현재 상태를 비교한다."""
        if not stored:
            return {"is_stale": None, "changed_sections": [],
                    "note": "이 결과에는 입력 지문이 없다 (구버전 파일)"}
        cur = self.inputs_fingerprint()
        changed = sorted(k for k in cur if cur[k] != stored.get(k))
        names = {"scenario": "시나리오(수량·분기·마진 등)",
                 "materials": "소재 선택", "gates": "승인 게이트",
                 "mapping": "세그먼트 매핑", "repairs": "부피 복구",
                 "bom_approvals": "BOM 승인 상태"}
        return {
            "is_stale": bool(changed),
            "changed_sections": [names.get(c, c) for c in changed],
            "note": ("입력이 바뀌어 이 결과는 낡았습니다. 다시 계산하세요."
                     if changed else None),
        }

    def version_key(self):
        """이 원가가 어떤 전제 위에서 나왔는지 못박는다.

        한 값이라도 바뀌면 다른 원가다. 무엇이 선언됐고 무엇이 아직 없는지를
        함께 남겨야, 나중에 이 숫자를 다시 볼 때 비교 가능한지 알 수 있다.
        """
        sc = self.state.get("scenario") or {}
        key = {
            "style_id": sc.get("style_id") or self.pid,
            "colorway": sc.get("colorway"),
            "size_range": sc.get("size_range"),
            "reference_size_label": sc.get("reference_size_label"),
            "factory": sc.get("factory"),
            "country": sc.get("country"),
            "order_quantity": sc.get("order_quantity"),
            "quarter": sc.get("quarter"),
            "currency": sc.get("currency"),
            "supplier": sc.get("supplier"),
            "quote_version": sc.get("quote_version"),
            "incoterm": sc.get("incoterm"),
            "construction": sc.get("construction"),
        }
        missing = sorted(k for k, v in key.items() if v in (None, ""))
        return {"key": key, "undeclared": missing,
                "comparable": not missing,
                "note": ("선언되지 않은 항목이 있으면 다른 원가와 같은 조건에서 "
                         "비교했다고 말할 수 없다.")}

    def approve_bom(self, actor, evidence, line_ids=None):
        """규칙이 제안한 숨은 BOM 라인을 엔지니어가 승인한다.

        승인 전에는 그 금액이 '확인된 소계' 에 들어가지 않는다. 누가 무슨
        근거로 올렸는지 남기지 않으면 승인이 아니라 그냥 플래그가 된다.
        """
        actor = (actor or "").strip()
        evidence = (evidence or "").strip()
        if not actor or not evidence:
            raise ValueError("승인자(actor)와 근거(evidence)가 필요합니다.")
        lines = self.state.get("bom") or []
        if not lines:
            raise ValueError("BOM 이 없습니다. 먼저 BOM 을 생성하세요.")
        log = self.state.setdefault("bom_approval_log", [])
        n = 0
        for l in lines:
            if line_ids and l.get("line_id") not in line_ids:
                continue
            if l.get("approval_status") != "rule_proposed":
                continue
            l["approval_status"] = "engineer_approved"
            l["approved_by"] = actor
            l["approved_evidence"] = evidence
            l["approved_at"] = _now()
            n += 1
        log.append({"actor": actor, "evidence": evidence, "lines": n,
                    "at": _now()})
        self.state["gates"]["hidden_bom_approved"] = bool(lines) and not any(
            l.get("approval_status") == "rule_proposed" for l in lines)
        self.save()
        return {"approved_lines": n,
                "hidden_bom_approved": self.state["gates"]["hidden_bom_approved"]}

    def material_options(self):
        """파트별로 고를 수 있는 소재 후보. 단가가 있는 것만 준다."""
        specs = catalog.material_specs()
        prices = catalog.quarterly_prices()
        q = self.state["scenario"]["quarter"]
        out = []
        for spec, d in sorted(specs.items()):
            row = prices.get((q, spec))
            if not row and not d.get("price_proxy"):
                continue
            out.append({
                "material_spec": spec, "description": d.get("description"),
                "form": d.get("form"),
                "uom": (row or {}).get("uom"),
                "p50": (row or {}).get("p50"),
                "eligibility": (row or {}).get("eligibility"),
                "confidence": (row or {}).get("confidence"),
                "source_url": (row or {}).get("source_url"),
            })
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
                              repairs=self.state.get("repairs"),
                              materials=self.state.get("materials"))
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
        coverage = costing.evidence_coverage(cl)
        result = {"scenario": sc, "lines": cl, "rollup": rollup, "grade": gr,
                  "mass_balance": mass, "evidence_coverage": coverage,
                  "version_key": self.version_key(),
                  "inputs_fingerprint": self.inputs_fingerprint(),
                  "computed_at": _now()}
        self.state["cost"] = {"rollup": rollup, "grade": gr,
                              "computed_at": result["computed_at"]}
        self.ensure_dir()
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
