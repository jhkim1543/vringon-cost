# -*- coding: utf-8 -*-
"""예시 디자인 하나를 실제 생성 엔진으로 끝까지 구워 프로젝트로 만든다.

    python tools/build_example.py <project_id> <image_path> [length_mm]

생성(30) + 세그멘테이션(40) + 메시 완성(50) 크레딧이 실제로 소모된다.
결과: 3D 생성 → 파트 분리 → 파트 완성 → 캘리브레이션 → 매핑 확정 →
부피 복구 → BOM → 원가까지 전부 계산된 상태의 프로젝트.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import pipeline  # noqa: E402
from mesh_provider import MeshProvider  # noqa: E402
from config import ASSETS  # noqa: E402


def main():
    pid = sys.argv[1]
    image = Path(sys.argv[2])
    length_mm = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0

    c = MeshProvider()
    print("잔액:", c.balance(), flush=True)

    p = pipeline.Project(pid)
    dst = ASSETS / f"{pid}{image.suffix.lower()}"
    if image.resolve() != dst.resolve():
        shutil.copy2(image, dst)
    p.state["input_image"] = dst.name
    p.save()

    log = lambda s, pr: print(f"  {s} {pr}%", flush=True) if pr % 20 == 0 else None

    print("1/3 3D 생성", flush=True)
    gen = c.image_to_glb(dst, p.dir / "raw_model.glb", on_progress=log)
    (p.dir / "generate_task.json").write_text(
        json.dumps(gen["raw"], ensure_ascii=False, indent=1), encoding="utf-8")
    p._mark("generate3d", "done", task_id=gen["task_id"])

    print("2/3 파트 분리", flush=True)
    seg = c.segment_to_glb(gen["task_id"], p.dir / "segmented.glb", on_progress=log)
    (p.dir / "segment_task.json").write_text(
        json.dumps(seg["raw"], ensure_ascii=False, indent=1), encoding="utf-8")
    p._mark("segment3d", "done", task_id=seg["task_id"])

    print("3/3 파트 완성 (부피 복구용)", flush=True)
    tid = c.mesh_complete(seg["task_id"])
    data = c.wait(tid, on_progress=log)
    c.download_model(data, p.dir / "completed.glb")
    (p.dir / "complete_task.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print("파이프라인 계산", flush=True)
    p.propose_landmarks()
    p.calibrate(length_mm, confirmed=True)
    p.propose_mapping()
    p.confirm_mapping(confirm_all=True)
    rep = p.repair_volumes()
    for sid, v in rep.items():
        sn = v.get("sensitivity") or {}
        print(f"  복구 {sid} {v.get('canonical_part', '?'):18s} "
              f"{'OK' if v.get('ok') else '실패'} tier={v.get('tier')} "
              f"CV={(sn.get('cv') or 0) * 100:.1f}%", flush=True)
    p.build_bom()
    r = p.estimate()
    p.viewer_glb(force=True)

    ru, mb = r["rollup"], r["mass_balance"]
    print(f"완료: 소재 P50 ${ru['known_cost_subtotal']['p50']:.3f} "
          f"({ru['coverage']['priced_lines']}/{ru['coverage']['bom_lines']}) | "
          f"완제품 {mb['finished_pair_mass_g']:.0f}g -> {mb['verdict']}")
    print("잔액:", c.balance())


if __name__ == "__main__":
    main()
