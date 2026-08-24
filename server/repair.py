# -*- coding: utf-8 -*-
"""Mesh completion / repair — 계획서 §5.5 의 fallback.

Tripo 세그멘테이션 결과는 파트가 열린 껍질이라 부피가 나오지 않는다.
그래도 midsole·outsole 은 부피 없이는 원가가 안 나온다. 그래서 복구 경로를 둔다.

복구는 '측정'이 아니다. 어떤 방법으로 닫았는지, 부피가 얼마나 달라졌는지를
결과에 남기고 신뢰도를 낮춘다. 조용히 watertight 로 바꿔치기하지 않는다.
"""
import numpy as np
import trimesh


def _try_manifold(mesh, voxel_pitch):
    """복셀 리메시로 닫힌 솔리드를 만든다.

    얇은 껍질을 두께 있는 솔리드로 바꾸는 가장 안정적인 방법이다.
    pitch 가 작을수록 형상은 살지만 무거워진다.
    """
    vg = mesh.voxelized(pitch=voxel_pitch).fill()
    solid = vg.marching_cubes
    # marching_cubes 는 복셀 인덱스 좌표계 메시를 준다. 그리드 변환을 적용하지
    # 않으면 부피가 pitch^-3 배로 부풀어 미드솔이 수백 m³ 가 된다.
    solid.apply_transform(vg.transform)
    solid.merge_vertices()
    # trimesh 5 에서 remove_degenerate_faces 가 빠졌다. update_faces 로 대체한다.
    try:
        solid.update_faces(solid.nondegenerate_faces())
    except Exception:
        pass
    solid.remove_unreferenced_vertices()
    solid.fix_normals()
    return solid


def repair_to_solid(mesh, target_voxels=110):
    """열린 파트를 닫힌 솔리드로 만들고 방법·오차를 함께 돌려준다.

    반환 {ok, mesh, method, raw_volume, note, confidence_penalty}
    """
    steps = []

    # 1) 구멍이 작으면 단순 채우기로 끝난다.
    m = mesh.copy()
    m.merge_vertices()
    try:
        m.fill_holes()
        m.fix_normals()
    except Exception as e:
        steps.append(f"fill_holes 실패: {e}")
    if m.is_watertight and m.volume > 0:
        return {
            "ok": True, "mesh": m, "method": "fill_holes",
            "raw_volume": float(m.volume),
            "note": "열린 경계를 직접 메움. 형상 변화 거의 없음",
            "confidence_penalty": 1, "steps": steps,
        }
    steps.append("fill_holes 후에도 닫히지 않음 -> 복셀 리메시")

    # 2) 복셀 리메시. 파트 크기에 맞춰 pitch 를 정한다.
    diag = float(np.linalg.norm(mesh.bounds[1] - mesh.bounds[0]))
    pitch = diag / max(target_voxels, 20)
    for k in (1.0, 1.6, 2.5):
        try:
            solid = _try_manifold(mesh, pitch * k)
        except Exception as e:
            steps.append(f"pitch×{k} 실패: {e}")
            continue
        if solid.is_watertight and solid.volume > 0:
            steps.append(f"복셀 pitch={pitch*k:.5f} 로 닫힘 (face {solid.faces.shape[0]})")
            return {
                "ok": True, "mesh": solid, "method": f"voxel_remesh(pitch={pitch*k:.5f})",
                "raw_volume": float(solid.volume),
                "note": ("복셀 리메시로 닫은 솔리드. 얇은 껍질이 두께를 얻어 "
                         "실제 소재 부피보다 클 수 있음. 승인 sole CAD 로 대체 권장"),
                "confidence_penalty": 2, "steps": steps,
            }
        steps.append(f"pitch×{k}: watertight={solid.is_watertight} vol={solid.volume:.6g}")

    return {"ok": False, "mesh": None, "method": None, "raw_volume": None,
            "note": "복구 실패. sole CAD 또는 승인 recipe 비율이 필요",
            "confidence_penalty": None, "steps": steps}


def shell_volume(mesh, thickness_mm, scale_mm_per_unit):
    """대안: 껍질 면적 × 소재 두께로 부피를 잡는다.

    복셀 리메시가 실패하거나, 파트가 실제로 '두께 있는 시트'일 때 쓴다.
    두께는 3D 가 아니라 MaterialSpec 에서 온다 (계획서 §5.6).
    """
    area_mm2 = float(mesh.area) * scale_mm_per_unit ** 2
    # 껍질은 앞뒤 양면이 다 잡히므로 절반만 쓴다.
    vol_mm3 = area_mm2 / 2.0 * float(thickness_mm)
    return {
        "raw_volume_m3": vol_mm3 / 1e9,
        "method": f"shell_area×thickness({thickness_mm}mm)",
        "note": "양면 껍질 가정으로 표면적의 절반에 두께를 곱함",
        "confidence_penalty": 2,
    }
