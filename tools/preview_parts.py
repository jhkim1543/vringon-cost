# -*- coding: utf-8 -*-
"""세그멘테이션 파트를 실루엣으로 렌더해 눈으로 확인한다.

    python tools/preview_parts.py <segmented.glb> <out.png>

파트 라벨이 seg_N 뿐이라 canonical 매핑 규칙을 설계하려면
각 파트가 실제로 무엇인지 봐야 한다.
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import trimesh
import geometry as G


def frame(whole):
    """x=장축(toe+), y=up, z=side 의 정규 프레임을 만든다."""
    lm = G.long_axis_landmarks(whole)
    x = np.array(lm["axis"], dtype=float)
    if (np.array(lm["toe"]) - np.array(lm["heel"])) @ x < 0:
        x = -x
    # glTF 는 Y-up 이다. 장축에 직교하도록 세워서 쓴다.
    up = np.array([0.0, 1.0, 0.0])
    up = up - (up @ x) * x
    up /= np.linalg.norm(up)
    return np.vstack([x, up, np.cross(x, up)])


def silhouette(ax, mesh, F, c, plane, color, title):
    """faces 를 2D 폴리곤으로 투영해 실루엣을 그린다."""
    i, j = plane
    V = (mesh.vertices - c) @ F.T
    tri = V[mesh.faces][:, :, [i, j]]
    # 면이 너무 많으면 표본만 그린다 (실루엣 형태는 유지된다)
    if len(tri) > 60000:
        idx = np.random.default_rng(0).choice(len(tri), 60000, replace=False)
        tri = tri[idx]
    ax.add_collection(PolyCollection(tri, facecolors=color, edgecolors="none", alpha=0.55))
    ax.set_title(title, fontsize=8)
    ax.set_aspect("equal")
    ax.axis("off")


def main():
    glb = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "parts_preview.png"
    sc = G.load_scene(glb)
    parts = G.scene_parts(sc)
    whole = trimesh.util.concatenate(list(parts.values()))
    F = frame(whole)
    c = whole.vertices.mean(0)
    W = (whole.vertices - c) @ F.T
    lo, hi = W.min(0), W.max(0)

    order = sorted(parts.items(), key=lambda kv: -kv[1].area)
    tota = sum(m.area for m in parts.values())
    cmap = plt.get_cmap("tab10")

    cells = [("WHOLE", whole, "0.35")] + [
        (n, m, cmap(k % 10)) for k, (n, m) in enumerate(order)
    ]
    ncol = 4
    nrow = -(-len(cells) // ncol)
    # 한 셀에 side/plan 두 장을 위아래로 넣는다
    fig, axes = plt.subplots(nrow * 2, ncol, figsize=(3.3 * ncol, 2.4 * nrow), dpi=140)
    axes = np.atleast_2d(axes)
    for ax in axes.ravel():
        ax.axis("off")

    for k, (name, m, col) in enumerate(cells):
        r, cc = divmod(k, ncol)
        pct = "" if name == "WHOLE" else f"  {100*m.area/tota:.1f}%  f={m.faces.shape[0]//1000}k"
        for sub, (plane, tag) in enumerate([((0, 1), "side (length x height)"),
                                            ((0, 2), "plan (length x width)")]):
            ax = axes[r * 2 + sub][cc]
            ax.axis("off")
            if name != "WHOLE":
                silhouette(ax, whole, F, c, plane, "0.88", "")
            silhouette(ax, m, F, c, plane, col,
                       f"{name}{pct}" if sub == 0 else f"({tag} view)")
            ax.set_xlim(lo[0], hi[0])
            ax.set_ylim(lo[plane[1]], hi[plane[1]])

    fig.suptitle("Segmentation parts   upper row = side view (length x height), "
                 "lower row = plan view (length x width)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    print("saved:", out)


if __name__ == "__main__":
    main()
