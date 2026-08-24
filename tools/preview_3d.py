# -*- coding: utf-8 -*-
"""파트를 3D 셰이딩으로 렌더해 무엇인지 확정한다.

    python tools/preview_3d.py <segmented.glb> <out.png> [face_budget]

실루엣만으로는 파트 판독이 흔들려서, 데시메이션 후 실제 음영을 준다.
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import trimesh
import geometry as G


def decimate(mesh, budget):
    if mesh.faces.shape[0] <= budget:
        return mesh
    try:
        import fast_simplification as fs
        v, f = fs.simplify(np.asarray(mesh.vertices, dtype=np.float32),
                           np.asarray(mesh.faces, dtype=np.int32),
                           target_count=budget)
        return trimesh.Trimesh(vertices=v, faces=f, process=False)
    except Exception as e:
        print("  decimate 실패, 면 표본으로 대체:", e)
        idx = np.random.default_rng(0).choice(mesh.faces.shape[0], budget, replace=False)
        return mesh.submesh([idx], append=True)


def shade(ax, mesh, base_rgb, alpha=1.0, light=np.array([0.4, 0.8, 0.45])):
    tri = mesh.vertices[mesh.faces]
    n = mesh.face_normals
    lam = np.clip(n @ (light / np.linalg.norm(light)), 0, 1)
    shade_v = 0.30 + 0.70 * lam
    cols = np.clip(np.array(base_rgb)[None, :] * shade_v[:, None], 0, 1)
    # 카메라에서 먼 면부터 그린다
    order = np.argsort(tri[:, :, 2].mean(axis=1))
    pc = Poly3DCollection(tri[order], facecolors=cols[order], edgecolors="none", alpha=alpha)
    ax.add_collection3d(pc)


def main():
    global ELEV, AZIM
    glb, out = sys.argv[1], sys.argv[2]
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 6000
    # 정규 프레임에서 (mpl_x,mpl_y,mpl_z)=(길이,폭,높이) 이므로
    # elev=0, azim=-90 이 정측면도가 된다.
    ELEV = float(sys.argv[4]) if len(sys.argv) > 4 else 8.0
    AZIM = float(sys.argv[5]) if len(sys.argv) > 5 else -88.0

    sc = G.load_scene(glb)
    parts = G.scene_parts(sc)
    whole0 = trimesh.util.concatenate(list(parts.values()))

    # 정규 프레임으로 회전시킨다. matplotlib 3D 는 Z-up 이므로
    # (mpl_x, mpl_y, mpl_z) = (길이, 폭, 높이) 가 되도록 행을 배치한다.
    import canonical as C
    F = C.canonical_frame(whole0)
    R = np.vstack([F[0], F[2], F[1]])          # 길이, 폭, 높이
    T = np.eye(4)
    T[:3, :3] = R
    parts = {n: m.copy().apply_transform(T) for n, m in parts.items()}
    whole = trimesh.util.concatenate(list(parts.values()))
    lo, hi = whole.bounds
    ctr, rad = (lo + hi) / 2, float(np.linalg.norm(hi - lo)) / 2
    tota = sum(m.area for m in parts.values())

    order = sorted(parts.items(), key=lambda kv: -kv[1].area)
    cmap = plt.get_cmap("tab10")
    ghost = decimate(whole, budget)

    ncol = 4
    nrow = -(-(len(order) + 1) // ncol)
    fig = plt.figure(figsize=(3.6 * ncol, 3.2 * nrow), dpi=140)

    cells = [("WHOLE", ghost, (0.55, 0.55, 0.58))] + [
        (n, decimate(m, budget), cmap(k % 10)[:3]) for k, (n, m) in enumerate(order)
    ]
    for k, (name, m, col) in enumerate(cells):
        ax = fig.add_subplot(nrow, ncol, k + 1, projection="3d")
        if name != "WHOLE":
            shade(ax, ghost, (0.86, 0.86, 0.88), alpha=0.13)
            pct = f"  {100*dict(order)[name].area/tota:.1f}%"
        else:
            pct = ""
        shade(ax, m, col)
        ax.set_xlim(ctr[0] - rad, ctr[0] + rad)
        ax.set_ylim(ctr[1] - rad, ctr[1] + rad)
        ax.set_zlim(ctr[2] - rad, ctr[2] + rad)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=ELEV, azim=AZIM)
        ax.set_axis_off()
        ax.set_title(f"{name}{pct}", fontsize=10)

    fig.suptitle("Segmentation parts - shaded 3D (world axes, glTF Y-up)", fontsize=12)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    print("saved:", out)


if __name__ == "__main__":
    main()
