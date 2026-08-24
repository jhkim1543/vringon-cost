# -*- coding: utf-8 -*-
"""3D Metric Calibration 과 Mesh QA.

계획서 §5의 핵심 제약을 코드로 강제한다.

1. 사용자가 입력하는 값은 "사이즈 260"이 아니라 실제 외부 outsole toe–heel 길이(mm).
2. uniform scale s = L_target / L_raw. length×s, area×s², volume×s³.
3. 신발 전체 shell 의 부피는 소재 부피가 아니다. watertight·winding·양수부피를
   모두 통과한 닫힌 솔리드 파트에만 volume-to-mass 를 허용한다.
4. 소재 두께는 3D와 함께 scale하지 않는다 (MaterialSpec 소관).
"""
import math

import numpy as np
import trimesh

# volume-to-mass 를 허용할 canonical part (계획서 §5.5).
VOLUME_ALLOWED_PARTS = {
    "Midsole Carrier", "Midsole Insert", "Outsole Rubber", "Rubber Pod",
    "TPU Cage/Heel Clip", "Performance Plate", "Arch Support",
    "Toe Bumper/Foxing",
}


# ── 로딩 ──────────────────────────────────────────────────────────────────
def load_scene(path):
    """GLB를 Scene으로 읽는다. 파트 이름을 보존하기 위해 concatenate하지 않는다."""
    obj = trimesh.load(path, force="scene", process=False)
    if isinstance(obj, trimesh.Trimesh):
        sc = trimesh.Scene()
        sc.add_geometry(obj, geom_name="mesh_0")
        return sc
    return obj


def scene_parts(scene):
    """Scene -> {이름: Trimesh}. 노드 변환을 적용한 월드 좌표 사본.

    변환을 빼먹으면 모든 파트가 로컬 원점에 겹쳐 쌓여서, 파트별 위치 특징이
    전부 한가운데로 수렴한다. 조용히 넘어가지 말고 확실히 실패시킨다.
    """
    parts = {}
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        geom = scene.geometry.get(gname)
        if not isinstance(geom, trimesh.Trimesh) or geom.faces.shape[0] == 0:
            continue
        m = geom.copy()
        m.apply_transform(T)
        key = gname if gname not in parts else f"{gname}@{node}"
        parts[key] = m
    if not parts:
        raise ValueError("씬에서 메시 노드를 찾지 못했습니다")
    return parts


def combined_mesh(scene):
    parts = scene_parts(scene)
    if not parts:
        raise ValueError("메시가 없는 GLB입니다")
    return trimesh.util.concatenate(list(parts.values()))


# ── 장축 탐색 / landmark 제안 ─────────────────────────────────────────────
def ground_up(mesh, axis):
    """접지면 법선으로 up 을 구한다.

    Tripo 결과물은 월드 축에 정렬돼 있지 않다. 볼록껍질에서 장축에 직교하는
    어느 방향을 향하는 면적이 최대인 쪽이 아웃솔 접지면이고 그 반대가 up 이다.
    """
    b1 = np.eye(3)[int(np.argmin(np.abs(axis)))]
    b1 = b1 - (b1 @ axis) * axis
    b1 /= np.linalg.norm(b1)
    b2 = np.cross(axis, b1)

    hull = mesh.convex_hull
    N, A = hull.face_normals, hull.area_faces
    cos20 = float(np.cos(np.radians(20.0)))
    best, down = -1.0, b1
    for deg in range(0, 360, 3):
        th = np.radians(deg)
        u = np.cos(th) * b1 + np.sin(th) * b2
        a = float(A[(N @ u) > cos20].sum())
        if a > best:
            best, down = a, u
    up = -down
    up = up - (up @ axis) * axis
    return up / np.linalg.norm(up)


def shoe_frame(mesh):
    """신발의 정규 프레임을 만든다: axis(toe+), up, side.

    순서가 중요하다. up 은 toe/heel 을 몰라도 접지면에서 구할 수 있고,
    toe/heel 은 up 을 알아야 구할 수 있다 (뒤꿈치 쪽이 더 높다).
    """
    V = np.asarray(mesh.vertices, dtype=np.float64)
    c = V.mean(axis=0)
    X = V - c
    w, vecs = np.linalg.eigh(np.cov(X.T))
    axis = vecs[:, int(np.argmax(w))]

    up = ground_up(mesh, axis)

    t = X @ axis
    h = X @ up
    span = t.max() - t.min()
    # 양 끝 12% 구간의 높이로 toe/heel 을 가른다. 굽·칼라가 있는 뒤가 더 높다.
    lo_end = h[t < t.min() + 0.12 * span]
    hi_end = h[t > t.max() - 0.12 * span]
    lo_h = float(np.percentile(lo_end, 95)) if lo_end.size else 0.0
    hi_h = float(np.percentile(hi_end, 95)) if hi_end.size else 0.0
    # axis 는 toe 를 향해야 한다. +axis 끝이 더 높으면 그쪽이 heel 이므로 뒤집는다.
    if hi_h > lo_h:
        axis = -axis
    return axis, up, np.cross(axis, up), c


def long_axis_landmarks(mesh):
    """장축 양 끝을 toe/heel 후보로 제안한다.

    자동 제안일 뿐이며 사용자 확인 전에는 확정하지 않는다 (계획서 §5.2).
    """
    V = np.asarray(mesh.vertices, dtype=np.float64)
    axis, up, side, c = shoe_frame(mesh)
    t = (V - c) @ axis
    toe = V[int(np.argmax(t))]
    heel = V[int(np.argmin(t))]
    return {
        "axis": axis.tolist(),
        "up": up.tolist(),
        "toe": [float(v) for v in toe],
        "heel": [float(v) for v in heel],
        "raw_length": float(np.linalg.norm(np.asarray(toe) - np.asarray(heel))),
        "auto": True,
        "confirmed": False,
    }


def raw_length(toe, heel):
    return float(np.linalg.norm(np.asarray(toe, dtype=float) - np.asarray(heel, dtype=float)))


# ── 스케일 ────────────────────────────────────────────────────────────────
def calibration(target_length_mm, toe, heel, target_width_mm=None, raw_width=None):
    """s = L_target / L_raw 와 파생 계수를 만든다.

    길이만 있으면 uniform scale. 폭을 AI로 임의 보정하지 않는다 (계획서 §5.4).
    """
    L_raw = raw_length(toe, heel)
    if L_raw <= 0:
        raise ValueError("toe·heel 두 점이 같습니다. landmark를 다시 지정하세요.")
    s = float(target_length_mm) / L_raw

    conf = "B"          # 길이만 확인 -> B
    anisotropic = None
    if target_width_mm and raw_width:
        conf = "A"      # 길이+폭 -> A 후보
        anisotropic = {"width_scale": float(target_width_mm) / float(raw_width)}

    return {
        "target_length_mm": float(target_length_mm),
        "raw_length": L_raw,
        "scale": s,                      # mm / model unit
        "area_scale": s * s,             # mm² / model unit²
        "volume_scale": s ** 3,          # mm³ / model unit³
        "measurement_type": "External outsole toe-to-heel",
        "confidence": conf,
        "anisotropic": anisotropic,
        "toe": [float(v) for v in toe],
        "heel": [float(v) for v in heel],
    }


def to_si(raw_value, kind, cal):
    """모델 단위 -> SI. kind: length|area|volume"""
    s = cal["scale"]
    if kind == "length":
        return raw_value * s / 1_000.0            # m
    if kind == "area":
        return raw_value * s * s / 1_000_000.0    # m²
    if kind == "volume":
        return raw_value * s ** 3 / 1_000_000_000.0  # m³
    raise ValueError(kind)


# ── Mesh QA ───────────────────────────────────────────────────────────────
def mesh_qa(mesh):
    """부피를 신뢰할 수 있는지 판정한다.

    trimesh 의 volume 은 닫힌 표면을 전제로 한다. 열린 메시의 volume 은
    의미가 없으므로 여기서 막는다.
    """
    watertight = bool(mesh.is_watertight)
    winding = bool(mesh.is_winding_consistent)
    vol = float(mesh.volume) if watertight else 0.0
    positive = watertight and vol > 0

    reasons = []
    if not watertight:
        reasons.append("not watertight (열린 경계 존재)")
    if not winding:
        reasons.append("winding 불일치")
    if watertight and vol <= 0:
        reasons.append("부피가 0 이하 (법선 반전 가능)")

    return {
        "watertight": watertight,
        "winding_consistent": winding,
        "is_volume": positive,
        "raw_volume": abs(vol),
        "raw_area": float(mesh.area),
        "faces": int(mesh.faces.shape[0]),
        "vertices": int(mesh.vertices.shape[0]),
        "euler_number": int(mesh.euler_number),
        "body_count": int(mesh.body_count),
        "blocked_reasons": reasons,
    }


def part_metrics(mesh, cal, canonical_part=None):
    """파트 하나의 metric 지표. volume 은 QA·파트유형 게이트를 통과해야만 채운다."""
    qa = mesh_qa(mesh)
    ext = mesh.bounds[1] - mesh.bounds[0]

    m = {
        "qa": qa,
        "surface_area_m2": to_si(qa["raw_area"], "area", cal),
        "bbox_mm": [float(v * cal["scale"]) for v in ext],
        "volume_m3": None,
        "volume_status": None,
    }

    allowed = canonical_part is None or canonical_part in VOLUME_ALLOWED_PARTS
    if not allowed:
        m["volume_status"] = "not_a_solid_part"   # shell 부피는 소재 부피가 아니다
    elif not qa["is_volume"]:
        m["volume_status"] = "blocked:" + "; ".join(qa["blocked_reasons"])
    else:
        m["volume_m3"] = to_si(qa["raw_volume"], "volume", cal)
        m["volume_status"] = "ok"
    return m


# ── Interface / seam ──────────────────────────────────────────────────────
def open_boundary_length(mesh):
    """열린 경계(테두리) 길이 합. 재단 패널의 둘레·시접 추정에 쓴다."""
    edges = mesh.edges_sorted
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    border = uniq[counts == 1]
    if len(border) == 0:
        return 0.0
    v = np.asarray(mesh.vertices)
    seg = v[border[:, 0]] - v[border[:, 1]]
    return float(np.linalg.norm(seg, axis=1).sum())


def contact_interface(mesh_a, mesh_b, tol_ratio=0.01):
    """두 파트의 근접 접촉을 seam/bond 후보로 본다.

    엄밀한 접합면 계산이 아니라 C1 단계의 proxy이며, 반환값의 method 로
    그 사실을 남긴다. tol 은 모델 대각선 길이 비율로 정한다.
    """
    diag = float(np.linalg.norm(mesh_a.bounds[1] - mesh_a.bounds[0]))
    tol = diag * tol_ratio
    try:
        pq = trimesh.proximity.ProximityQuery(mesh_b)
        d = np.abs(pq.signed_distance(mesh_a.vertices))
    except Exception:
        return None
    near = d < tol
    if near.sum() < 3:
        return None

    # 접촉 정점이 속한 면의 면적 합을 bond area proxy로 쓴다.
    fmask = near[mesh_a.faces].any(axis=1)
    area = float(mesh_a.area_faces[fmask].sum())
    # 접촉 영역의 외곽 길이를 seam 길이 proxy로 쓴다.
    sub = mesh_a.submesh([np.where(fmask)[0]], append=True) if fmask.any() else None
    seam = open_boundary_length(sub) if sub is not None else 0.0
    return {
        "raw_bond_area": area,
        "raw_seam_length": seam,
        "method": "proximity_proxy",
        "tolerance": tol,
    }
