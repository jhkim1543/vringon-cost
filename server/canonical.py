# -*- coding: utf-8 -*-
"""Visual Segment -> Canonical Part 매핑.

계획서 §7.1의 핵심: 세그멘테이션 결과는 BOM이 아니다.
Tripo mesh_segmentation 은 기하 기반이라 라벨이 tripo_mesh_N 뿐이므로,
정규 프레임에서 뽑은 기하 특징으로 canonical part 후보를 제안한다.

제안은 어디까지나 ai_proposed 이며 사용자가 확정하기 전에는
confirmed=False 로 남는다 (계획서 §5.2, §17 Status 규약).
"""
import numpy as np
import trimesh
from scipy.optimize import linear_sum_assignment


# ── 정규 프레임 ────────────────────────────────────────────────────────────
def canonical_frame(whole, toe=None, heel=None):
    """x=장축(toe 방향 +), y=up, z=측면.

    up 은 접지면(볼록껍질 최대 대향 면적)에서, toe/heel 은 up 을 안 뒤
    양 끝 높이로 가른다. 사용자가 landmark 를 확정했으면 그 방향을 우선한다.
    """
    from geometry import shoe_frame

    x, up, side, _c = shoe_frame(whole)
    if toe is not None and heel is not None:
        if (np.asarray(toe, float) - np.asarray(heel, float)) @ x < 0:
            x, side = -x, -side
    return np.vstack([x, up, side])


def features(mesh, F, center, origin, extent, total_area):
    """파트 하나의 정규화 기하 특징. 위치는 전체 bbox 기준 0..1.

    origin 은 전체 메시의 프레임 내 최소점이다. 무게중심이 아니라 이 값을
    원점으로 써야 위치가 0..1 로 제대로 펼쳐진다.
    """
    P = (mesh.vertices - center) @ F.T
    lo, hi = P.min(0), P.max(0)
    a = (lo - origin) / extent
    b = (hi - origin) / extent
    span = b - a
    ext = hi - lo                       # 정규 프레임에서의 실제 치수
    s = np.sort(ext)
    # 채움률: 얇은 껍질·끈처럼 bbox 대비 표면이 적은 형상을 가려낸다.
    bbox_surf = 2 * (ext[0] * ext[1] + ext[1] * ext[2] + ext[0] * ext[2])
    return {
        "area_share": float(mesh.area / total_area),
        "len_lo": float(a[0]), "len_hi": float(b[0]), "len_span": float(span[0]),
        "len_center": float((a[0] + b[0]) / 2),
        "hgt_lo": float(a[1]), "hgt_hi": float(b[1]), "hgt_span": float(span[1]),
        "hgt_center": float((a[1] + b[1]) / 2),
        "wid_span": float(span[2]),
        "wid_offset": float(abs((a[2] + b[2]) / 2 - 0.5) * 2),
        "thinness": float(s[0] / max(s[2], 1e-9)),
        "fill": float(mesh.area / bbox_surf) if bbox_surf > 1e-12 else 0.0,
    }


# ── 시그니처 ───────────────────────────────────────────────────────────────
# (feature, lo, hi, weight). 범위를 벗어나면 거리에 비례해 점수가 깎인다.
# 길이 0 = heel, 1 = toe. 높이 0 = 접지면, 1 = 최상단.
SIGNATURES = {
    "Outsole Rubber": [
        ("hgt_center", 0.00, 0.14, 3.0), ("hgt_hi", 0.00, 0.30, 2.0),
        ("len_span", 0.25, 1.00, 1.0), ("thinness", 0.00, 0.35, 1.0),
    ],
    "Midsole Carrier": [
        ("hgt_center", 0.08, 0.35, 2.5), ("hgt_hi", 0.25, 0.70, 1.5),
        ("len_span", 0.70, 1.00, 2.5), ("area_share", 0.08, 0.60, 1.5),
    ],
    "Midsole Insert": [
        ("hgt_center", 0.15, 0.45, 2.0), ("len_span", 0.50, 1.00, 1.5),
        ("area_share", 0.005, 0.09, 2.0), ("wid_span", 0.00, 0.55, 1.0),
    ],
    "Vamp": [
        ("hgt_center", 0.35, 0.75, 2.0), ("len_span", 0.70, 1.00, 2.5),
        ("area_share", 0.12, 0.60, 2.5), ("wid_span", 0.50, 1.00, 1.0),
    ],
    # 텅은 한 장의 판이라 채움률이 높다. 끈과 갈리는 지점이 여기다.
    "Tongue Shell": [
        ("len_center", 0.35, 0.75, 2.0), ("len_span", 0.10, 0.55, 1.5),
        ("hgt_hi", 0.70, 1.00, 1.5), ("wid_span", 0.15, 0.75, 1.0),
        ("fill", 0.45, 1.00, 2.5), ("area_share", 0.02, 0.30, 1.0),
    ],
    "Collar Shell": [
        ("hgt_center", 0.50, 1.00, 2.5), ("len_center", 0.05, 0.45, 2.0),
        ("area_share", 0.02, 0.35, 1.0), ("hgt_hi", 0.80, 1.00, 1.5),
    ],
    # 삭라이너는 신발 안쪽 발바닥이라 아웃솔보다 확실히 높다.
    "Sockliner Cover": [
        ("hgt_center", 0.22, 0.50, 3.0), ("thinness", 0.00, 0.20, 2.0),
        ("wid_span", 0.35, 1.00, 1.0), ("area_share", 0.01, 0.20, 1.0),
    ],
    # 끈은 관 다발이라 bbox 대비 표면 채움률이 낮고 목선 위쪽 가운데에 있다.
    "Lace": [
        ("hgt_center", 0.65, 1.00, 3.0), ("len_center", 0.30, 0.80, 2.0),
        ("fill", 0.00, 0.45, 2.5), ("area_share", 0.01, 0.20, 1.0),
    ],
    "Webbing/Pull Tab": [
        ("len_span", 0.00, 0.15, 3.0), ("area_share", 0.000, 0.05, 2.0),
        ("hgt_center", 0.45, 1.00, 1.5), ("len_center", 0.00, 0.20, 2.0),
    ],
    "Mudguard/Toe Overlay": [
        ("len_center", 0.62, 1.00, 2.5), ("hgt_center", 0.10, 0.50, 1.5),
        ("area_share", 0.005, 0.12, 1.5),
    ],
    "Heel Overlay": [
        ("len_center", 0.00, 0.35, 2.5), ("hgt_center", 0.25, 0.70, 1.5),
        ("area_share", 0.005, 0.12, 1.5),
    ],
    "Logo/Graphic": [
        ("area_share", 0.000, 0.015, 2.5), ("thinness", 0.00, 0.25, 1.0),
        ("hgt_center", 0.30, 0.95, 1.0),
    ],
}


# 한 켤레에 여러 조각으로 나오는 파트와 허용 개수.
REPEATABLE = {
    "Outsole Rubber": 4,
    "Midsole Insert": 3,
    "Rubber Pod": 4,
    "Logo/Graphic": 3,
    "Mudguard/Toe Overlay": 2,
    "Heel Overlay": 2,
}


def _band(v, lo, hi):
    """밴드 점수.

    범위 안이면 1.0 을 주는 단순한 형태로는 여러 후보가 동시에 만점을 받아
    순위 차이가 사라진다. 중앙에 가까울수록 높게 주어 후보를 실제로 벌린다.
    밖으로 나가면 밴드 폭에 비례해 감쇠한다.
    """
    w = max(hi - lo, 1e-6)
    if lo <= v <= hi:
        d = abs(v - (lo + hi) / 2) / (w / 2)      # 0(중앙)..1(가장자리)
        return float(1.0 - 0.35 * d * d)
    d = (lo - v) if v < lo else (v - hi)
    return float(max(0.0, 0.65 - d / w))


def score(feat, sig):
    num = sum(w * _band(feat.get(f, 0.0), lo, hi) for f, lo, hi, w in sig)
    den = sum(w for *_, w in sig)
    return num / den if den else 0.0


# ── 매핑 ──────────────────────────────────────────────────────────────────
def propose(parts, toe=None, heel=None, allowed=None):
    """{세그먼트명: Trimesh} -> 세그먼트별 canonical 후보 (제안 상태).

    같은 canonical part 에 두 세그먼트가 몰리지 않도록 전역 최적 배정을 쓴다.
    """
    if not parts:
        return []
    whole = trimesh.util.concatenate(list(parts.values()))
    F = canonical_frame(whole, toe, heel)
    center = whole.vertices.mean(0)
    P = (whole.vertices - center) @ F.T
    origin = P.min(0)
    extent = P.max(0) - origin
    extent[extent < 1e-9] = 1e-9
    total_area = sum(m.area for m in parts.values()) or 1.0

    names = list(parts.keys())
    feats = {n: features(parts[n], F, center, origin, extent, total_area) for n in names}
    base = [c for c in SIGNATURES if allowed is None or c in allowed]

    # 실제 신발에는 같은 canonical part 가 여러 개 있다(아웃솔 패드, 오버레이 등).
    # 그런 파트는 열을 복제해 중복 배정을 허용하고, 나머지는 1:1 로 묶는다.
    cands = list(base)
    for c in base:
        if c in REPEATABLE:
            cands += [c] * (REPEATABLE[c] - 1)

    S = np.array([[score(feats[n], SIGNATURES[c]) for c in cands] for n in names])
    # 헝가리안은 비용 최소화이므로 부호를 뒤집는다.
    rows, cols = linear_sum_assignment(-S)
    assign = {int(r): int(c) for r, c in zip(rows, cols)}

    out = []
    for i, n in enumerate(names):
        # 열 복제 때문에 같은 이름이 여러 번 나온다. 이름 기준 최고점만 남긴다.
        by_name = {}
        for j, c in enumerate(cands):
            by_name[c] = max(by_name.get(c, -1.0), float(S[i, j]))
        ranked = [{"canonical_part": c, "score": round(s, 3)}
                  for c, s in sorted(by_name.items(), key=lambda kv: -kv[1])]

        j = assign.get(i)
        best = cands[j] if j is not None else ranked[0]["canonical_part"]
        best_score = by_name[best]
        # 신뢰도는 절대 점수만으로 정하지 않는다. 2위와 벌어진 만큼만 믿는다.
        second = next((s for c, s in
                       sorted(by_name.items(), key=lambda kv: -kv[1]) if c != best), 0.0)
        margin = max(0.0, best_score - second) / max(best_score, 1e-6)
        conf = best_score * (0.55 + 0.45 * min(1.0, margin / 0.25))

        out.append({
            "segment_id": n,
            "canonical_part": best,
            "confidence": round(conf, 3),
            "score": round(best_score, 3),
            "margin": round(margin, 3),
            # 낮으면 UI가 '확인 필요'로 띄운다. 확정 전에는 원가에 쓰지 않는다.
            "status": "ai_proposed" if conf >= 0.75 else "needs_review",
            "confirmed": False,
            "alternatives": ranked[:5],
            "features": feats[n],
        })
    out.sort(key=lambda d: -d["features"]["area_share"])
    return out
