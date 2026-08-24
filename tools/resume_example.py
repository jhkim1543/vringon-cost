# -*- coding: utf-8 -*-
"""중단된 예시 빌드를 생성 task id 부터 이어서 완성한다 (재과금 없음)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import pipeline
from mesh_provider import MeshProvider

pid = sys.argv[1]; length = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
p = pipeline.Project(pid)
c = MeshProvider()
gen = json.loads((p.dir / "generate_task.json").read_text(encoding="utf-8"))
tid = gen["task_id"]
print("이어서:", pid, "생성 task", tid, flush=True)
seg = c.segment_to_glb(tid, p.dir / "segmented.glb")
(p.dir / "segment_task.json").write_text(json.dumps(seg["raw"], ensure_ascii=False, indent=1), encoding="utf-8")
comp_tid = c.mesh_complete(seg["task_id"])
data = c.wait(comp_tid)
c.download_model(data, p.dir / "completed.glb")
p.propose_landmarks(); p.calibrate(length, confirmed=True)
p.propose_mapping(); p.confirm_mapping(confirm_all=True)
p.repair_volumes(); p.build_bom(); r = p.estimate(); p.viewer_glb(force=True)
ru, mb = r["rollup"], r["mass_balance"]
print(f"완료: 소재 P50 ${ru['known_cost_subtotal']['p50']:.3f} | 완제품 {mb['finished_pair_mass_g']:.0f}g -> {mb['verdict']}")
print("경고:", ru.get("sanity_warnings"))
