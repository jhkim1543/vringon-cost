# -*- coding: utf-8 -*-
"""Recipe 가 요구하는 proxy 치수를 실제 지오메트리에서 뽑는다.

레시피의 qty_method 는 last_bottom_area, tongue_area, bond_area 처럼
'제조에서 쓰는 치수'를 요구한다. 3D 에는 그런 이름의 값이 없으므로
측정 가능한 것은 측정하고, 아닌 것은 proxy 로 만들되 반드시 그렇다고 남긴다.

각 값은 {value, unit, method, source} 를 갖는다.
  method: measured   실제 파트 표면적/경계에서 직접 잼
          proxy      다른 측정값에서 비율로 유도 (C1 한정)
          blocked    입력이 없어 계산 불가
"""
import numpy as np
import trimesh
from scipy.spatial import ConvexHull

from geometry import to_si, open_boundary_length

UPPER_PARTS = {
    "Vamp", "Medial Quarter", "Lateral Quarter", "Eyestay", "Mudguard/Toe Overlay",
    "Heel Overlay", "Tongue Shell", "Collar Shell",
}
SOLE_PARTS = {"Midsole Carrier", "Midsole Insert", "Outsole Rubber", "Rubber Pod"}


def _m(value, unit, method, source, note=None):
    return {"value": float(value), "unit": unit, "method": method,
            "source": source, "note": note}


class GeometryContext:
    """확정된 세그먼트 매핑 + 캘리브레이션으로 제조 치수를 계산한다."""

    def __init__(self, parts, mapping, cal, frame):
        self.parts = parts                       # {segment_id: Trimesh}
        self.cal = cal
        self.F = frame
        # canonical part -> 세그먼트 목록
        self.by_part = {}
        for m in mapping:
            self.by_part.setdefault(m["canonical_part"], []).append(m["segment_id"])
        self.whole = trimesh.util.concatenate(list(parts.values()))
        self.center = np.asarray(self.whole.vertices).mean(0)
        P = (np.asarray(self.whole.vertices) - self.center) @ self.F.T
        self.origin = P.min(0)
        self.extent = P.max(0) - self.origin
        self.extent[self.extent < 1e-9] = 1e-9

    # ── 기본 도구 ────────────────────────────────────────────────────
    def _proj(self, mesh):
        return (np.asarray(mesh.vertices) - self.center) @ self.F.T

    def _norm(self, mesh):
        p = self._proj(mesh)
        return (p - self.origin) / self.extent

    def meshes_for(self, canonical_parts):
        out = []
        for cp in canonical_parts:
            for sid in self.by_part.get(cp, []):
                if sid in self.parts:
                    out.append(self.parts[sid])
        return out

    def area_m2(self, canonical_parts):
        """해당 canonical part 들의 실측 표면적 합 (m²)."""
        ms = self.meshes_for(canonical_parts)
        raw = sum(float(m.area) for m in ms)
        return to_si(raw, "area", self.cal), len(ms)

    # ── 레시피가 요구하는 치수 ────────────────────────────────────────
    def upper_proxy_area(self):
        a, n = self.area_m2(UPPER_PARTS)
        if n == 0:
            return _m(0, "m2", "blocked", "upper 세그먼트 없음")
        # 외피 표면적은 안쪽 면까지 포함될 수 있어 그대로는 패턴 면적이 아니다.
        return _m(a, "m2", "measured", f"upper 세그먼트 {n}개 표면적 합")

    def outer_proxy_area(self):
        return self.upper_proxy_area()

    def last_bottom_area(self):
        """라스트 바닥 면적 = 솔 유닛의 평면 투영 면적."""
        ms = self.meshes_for(SOLE_PARTS)
        if not ms:
            return _m(0, "m2", "blocked", "sole 세그먼트 없음")
        pts = np.vstack([self._proj(m)[:, [0, 2]] for m in ms])
        try:
            raw = float(ConvexHull(pts).volume)     # 2D 에서 volume = 면적
        except Exception:
            return _m(0, "m2", "blocked", "footprint 볼록껍질 실패")
        return _m(to_si(raw, "area", self.cal), "m2", "proxy",
                  "솔 평면투영 볼록껍질", "실제 라스트 바닥은 볼록하지 않아 약간 과대")

    def tongue_area(self):
        a, n = self.area_m2({"Tongue Shell"})
        if n:
            return _m(a, "m2", "measured", "Tongue Shell 표면적")
        up = self.upper_proxy_area()
        return _m(up["value"] * 0.10, "m2", "proxy", "upper 면적 × 0.10",
                  "텅 세그먼트가 없어 비율로 대체")

    def collar_area(self):
        a, n = self.area_m2({"Collar Shell"})
        if n:
            return _m(a, "m2", "measured", "Collar Shell 표면적")
        up = self.upper_proxy_area()
        return _m(up["value"] * 0.08, "m2", "proxy", "upper 면적 × 0.08")

    def _upper_zone_area(self, lo, hi, label):
        """upper 표면적 중 길이 [lo,hi] 구간이 차지하는 면적."""
        ms = self.meshes_for(UPPER_PARTS)
        if not ms:
            return _m(0, "m2", "blocked", "upper 세그먼트 없음")
        raw = 0.0
        for m in ms:
            h = self._norm(m)[:, 0]
            fh = h[m.faces].mean(axis=1)
            raw += float(m.area_faces[(fh >= lo) & (fh <= hi)].sum())
        return _m(to_si(raw, "area", self.cal), "m2", "measured",
                  f"upper 길이 {lo:.2f}–{hi:.2f} 구간 ({label})")

    def toe_proxy_area(self):
        return self._upper_zone_area(0.72, 1.00, "toe")

    def heel_proxy_area(self):
        return self._upper_zone_area(0.00, 0.30, "heel")

    def eyelet_zone_area(self):
        """레이스 주변 띠. 끈 세그먼트의 길이 범위를 그대로 쓴다."""
        laces = self.meshes_for({"Lace"})
        if laces:
            n = np.vstack([self._norm(m)[:, 0] for m in laces])
            lo, hi = float(n.min()), float(n.max())
        else:
            lo, hi = 0.35, 0.72
        return self._upper_zone_area(lo, hi, "eyelet/lacing")

    def overlay_area(self):
        a, n = self.area_m2({"Mudguard/Toe Overlay", "Heel Overlay", "Logo/Graphic"})
        if n == 0:
            return _m(0, "m2", "blocked", "overlay 세그먼트 없음")
        return _m(a, "m2", "measured", f"overlay 세그먼트 {n}개")

    def print_area(self):
        a, n = self.area_m2({"Logo/Graphic"})
        if n == 0:
            return _m(0, "m2", "blocked", "그래픽 세그먼트 없음")
        return _m(a, "m2", "measured", "Logo/Graphic 표면적")

    def coated_area(self):
        a, n = self.area_m2({"Midsole Carrier", "Midsole Insert"})
        if n == 0:
            return _m(0, "m2", "blocked", "midsole 세그먼트 없음")
        return _m(a, "m2", "measured", "midsole 외피 면적")

    def bond_area(self):
        """접착면 = 라스트 바닥 면적 + 측면 물림(roughing) 띠.

        정확한 접합면은 승인 patterns 이 있어야 나온다. C1 단계 proxy 다.
        """
        base = self.last_bottom_area()
        if base["method"] == "blocked":
            return base
        wall = self._upper_zone_area(0.00, 1.00, "bite line")
        # 물림 높이는 통상 8–15mm. 바닥 둘레 × 12mm 로 근사한다.
        peri = self.bite_line_length()
        extra = peri["value"] * 0.012 if peri["method"] != "blocked" else 0.0
        return _m(base["value"] + extra, "m2", "proxy",
                  "라스트 바닥 + 둘레×12mm 물림",
                  "승인 pattern 이 있으면 실제 접합면으로 대체")

    def bite_line_length(self):
        """어퍼–솔 물림선 길이 = 솔 평면투영 볼록껍질 둘레."""
        ms = self.meshes_for(SOLE_PARTS)
        if not ms:
            return _m(0, "m", "blocked", "sole 세그먼트 없음")
        pts = np.vstack([self._proj(m)[:, [0, 2]] for m in ms])
        try:
            h = ConvexHull(pts)
            raw = float(h.area)                 # 2D 에서 area = 둘레
        except Exception:
            return _m(0, "m", "blocked", "둘레 계산 실패")
        return _m(to_si(raw, "length", self.cal), "m", "proxy", "솔 평면투영 둘레")

    def seam_length(self):
        """봉제선 길이 proxy = 어퍼 패널들의 열린 경계 길이 합.

        Tripo 메시는 패널이 실제로 나뉘어 있지 않아 이 값은 하한이다.
        승인 DXF 가 들어오면 패턴 둘레 합으로 대체해야 한다.
        """
        ms = self.meshes_for(UPPER_PARTS)
        if not ms:
            return _m(0, "m", "blocked", "upper 세그먼트 없음")
        raw = sum(open_boundary_length(m) for m in ms)
        if raw <= 0:
            bl = self.bite_line_length()
            return _m(bl["value"] * 2.5, "m", "proxy", "물림선 길이 × 2.5",
                      "패널 경계가 없어 비율로 대체")
        return _m(to_si(raw, "length", self.cal), "m", "proxy",
                  "어퍼 패널 열린경계 합", "실제 봉제선은 DXF 필요")

    # ── 디스패치 ─────────────────────────────────────────────────────
    METHODS = {
        "last_bottom_area": "last_bottom_area",
        "outer_proxy_area": "outer_proxy_area",
        "upper_proxy_area": "upper_proxy_area",
        "tongue_area": "tongue_area",
        "collar_area": "collar_area",
        "toe_proxy_area": "toe_proxy_area",
        "heel_proxy_area": "heel_proxy_area",
        "eyelet_zone_area": "eyelet_zone_area",
        "overlay_area": "overlay_area",
        "print_area": "print_area",
        "coated_area": "coated_area",
        "bond_area": "bond_area",
        "seam_length": "seam_length",
    }

    def resolve(self, qty_method):
        fn = self.METHODS.get(qty_method)
        if fn is None:
            return _m(0, "", "blocked", f"qty_method '{qty_method}' 미구현")
        return getattr(self, fn)()
