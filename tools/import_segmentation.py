# -*- coding: utf-8 -*-
"""사내 세그멘테이션 모델 출력을 프로젝트로 들여온다.

    python tools/import_segmentation.py <project_id> <segmodel_dir>

segmodel_dir 에는 다음이 있어야 한다 (h100 추론 번들의 result 계약).
    shoe_proxy.glb                        추론 입력 메시
    source_display_face_labels_0based.npy face -> display part id (0=미배정)
    semantic_parts.json                   display part -> 클래스·신뢰도

동작: 같은 클래스의 display part 들을 face 단위로 합쳐 클래스당 한 메시를
만들고, 클래스명을 canonical part 로 매핑해 segmented.glb 와 model_mapping.json
을 프로젝트 폴더에 쓴다. 이후 파이프라인은 이 매핑을 기하 추정 대신 쓴다.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import trimesh
import geometry as geo  # noqa: E402

# 모델 클래스 -> canonical part (워크북 10_신발BOM마스터 표기).
# 모델이 더 세밀하게 나눠도 제조 BOM 기준으로 여기서 합친다.
CLASS_TO_CANONICAL = {
    "outsole": "Outsole Rubber",
    "midsole": "Midsole Carrier",
    "quarter-fabric": "Vamp",          # 원피스 메시 어퍼는 한 장으로 잡힌다
    "vamp": "Vamp",
    "tongue": "Tongue Shell",
    "laces": "Lace",
    "lining": "Vamp/Quarter Lining",   # 규칙 proxy 였던 라이닝이 실측이 된다
    "heeltap": "Heel Overlay",
    "loop": "Webbing/Pull Tab",
    "eyestay": "Eyestay",
    "collar": "Collar Shell",
    "toecap": "Mudguard/Toe Overlay",
}


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "DEMO-SEM-001"
    seg_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else \
        Path(__file__).resolve().parents[1] / "data" / "projects" / pid / "segmodel"
    proj_dir = Path(__file__).resolve().parents[1] / "data" / "projects" / pid
    proj_dir.mkdir(parents=True, exist_ok=True)

    labels = np.load(seg_dir / "source_display_face_labels_0based.npy")
    sem = json.loads((seg_dir / "semantic_parts.json").read_text(encoding="utf-8"))
    scene = geo.load_scene(seg_dir / "shoe_proxy.glb")
    mesh = trimesh.util.concatenate(list(geo.scene_parts(scene).values()))
    assert len(labels) == mesh.faces.shape[0], \
        f"라벨 {len(labels)} 와 face {mesh.faces.shape[0]} 수가 다르다"

    # display part id -> (클래스, 신뢰도, provenance)
    info = {p["display_part_id_1based"]: p for p in sem["parts"]}

    # 클래스별 face 모으기 (0 은 미배정)
    faces_by_class = defaultdict(list)
    stats = defaultdict(lambda: {"area_w_conf": 0.0, "area": 0.0,
                                 "components": 0, "provenance": set()})
    areas = mesh.area_faces
    for did in np.unique(labels):
        if did == 0:
            continue
        p = info.get(int(did))
        if p is None:
            continue
        cls = p["prompt"]
        idx = np.where(labels == did)[0]
        faces_by_class[cls].append(idx)
        a = float(areas[idx].sum())
        st = stats[cls]
        st["area_w_conf"] += a * float(p["confidence"])
        st["area"] += a
        st["components"] += 1
        st["provenance"].add(p["provenance"])

    unassigned = int((labels == 0).sum())
    total_faces = int(mesh.faces.shape[0])

    out_scene = trimesh.Scene()
    mapping = []
    for i, (cls, idx_list) in enumerate(
            sorted(faces_by_class.items(), key=lambda kv: -stats[kv[0]]["area"])):
        idx = np.concatenate(idx_list)
        sub = mesh.submesh([idx], append=True)
        sub.remove_unreferenced_vertices()
        name = f"seg_{i:02d}"
        out_scene.add_geometry(sub, geom_name=name, node_name=name)

        st = stats[cls]
        conf = st["area_w_conf"] / max(st["area"], 1e-12)
        canonical = CLASS_TO_CANONICAL.get(cls)
        mapping.append({
            "segment_id": name,
            "model_class": cls,
            "canonical_part": canonical or cls,
            "confidence": round(conf, 3),
            "components": st["components"],
            "provenance": sorted(st["provenance"]),
            "status": ("model_detected" if canonical and conf >= 0.7
                       else "needs_review"),
            "confirmed": False,
            "source": "internal_shoe_seg_v4pure",
            "unmapped_class": canonical is None,
        })

    out_scene.export(proj_dir / "segmented.glb")
    (proj_dir / "model_mapping.json").write_text(json.dumps({
        "source": "internal_shoe_seg_v4pure",
        "classes": len(faces_by_class),
        "display_parts": len(sem["parts"]),
        "unassigned_faces": unassigned,
        "face_coverage": 1.0 - unassigned / total_faces,
        "mapping": mapping,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"클래스 {len(faces_by_class)}개 (display part {len(sem['parts'])}개를 병합)")
    print(f"face 커버리지 {1 - unassigned / total_faces:.1%} (미배정 {unassigned})")
    for m in mapping:
        tag = "" if not m["unmapped_class"] else "  [매핑 없음]"
        print(f"  {m['segment_id']}  {m['model_class']:16s} -> "
              f"{m['canonical_part']:22s} conf={m['confidence']:.2f} "
              f"({m['components']}조각){tag}")


if __name__ == "__main__":
    main()
