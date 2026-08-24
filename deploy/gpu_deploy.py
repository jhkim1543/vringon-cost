# -*- coding: utf-8 -*-
"""사내 GPU 서버(jhkim 영역)로 배포한다.

    python deploy/gpu_deploy.py            코드만 갱신하고 재기동
    python deploy/gpu_deploy.py --meshes   원본 파트 메시까지 동기화 (410MB, 느림)
    python deploy/gpu_deploy.py --verify   배포하지 않고 살아있는지만 확인

코드(server, web, Procfile, requirements)와 가벼운 데이터(스펙·씨앗·예시·
프로젝트 상태/결과/뷰어)는 매번 올린다. 원본 파트 메시(segmented, completed)는
크고 잘 바뀌지 않으므로 --meshes 일 때만 올린다. 이 파일들이 없으면 예시
프로젝트는 보기만 되고 다시 계산이 안 된다.

키와 공급자 주소는 서버의 env 파일에만 있다. 이 스크립트는 그것을 건드리지
않는다. 다른 서비스와 공유하는 것은 아무것도 없다.
"""
import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "plushgpu"
REMOTE = "/data/jhkim/vringon-cost-svc"
BASE = "http://61.107.200.148:18452"
MESH_FILES = ("segmented.glb", "completed.glb")


def run(*args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", **kw)
    if r.returncode:
        sys.exit(f"실패: {' '.join(args[:3])}\n{r.stderr.strip()[:400]}")
    return r.stdout.strip()


def ssh(cmd):
    return run("ssh", "-o", "BatchMode=yes", HOST, cmd)


def scp(src, dst):
    run("scp", "-q", "-r", str(src), f"{HOST}:{dst}")


def deploy(with_meshes):
    print("번들 생성")
    run(sys.executable, str(ROOT / "deploy" / "eb_bundle.py"), cwd=str(ROOT))
    zipf = ROOT / "deploy" / "eb-bundle.zip"
    print(f"  {zipf.stat().st_size/1e6:.1f} MB 올리는 중")
    scp(zipf, f"{REMOTE}/bundle.zip")

    # app/ 을 새로 펴되, 서버에서 만들어진 프로젝트와 원본 메시는 지키다가
    # 되돌려 놓는다. 이것을 안 하면 라이브에서 생성한 결과가 배포마다 사라진다.
    print("전개 (서버 생성물 보존)")
    ssh(f"cd {REMOTE} && rm -rf keep && mv app/data/projects keep 2>/dev/null; "
        f"rm -rf app && mkdir app && cd app && "
        f"python3 -c \"import zipfile; zipfile.ZipFile('../bundle.zip').extractall('.')\" && "
        f"cd {REMOTE} && for p in keep/*; do n=$(basename $p); mkdir -p app/data/projects/$n; "
        f"cp -rn $p/* app/data/projects/$n/ 2>/dev/null; done; rm -rf keep")

    if with_meshes:
        print("원본 파트 메시 동기화")
        for proj in sorted((ROOT / "data" / "projects").iterdir()):
            for name in MESH_FILES:
                f = proj / name
                if f.exists():
                    scp(f, f"{REMOTE}/app/data/projects/{proj.name}/{name}")
                    print(f"  {proj.name}/{name}")
            segmodel = proj / "segmodel"
            if segmodel.is_dir():
                scp(segmodel, f"{REMOTE}/app/data/projects/{proj.name}/")

    print("재기동")
    ssh('pkill -u jhkim -f "[u]vicorn server.app:app" || true')
    ssh(f"setsid {REMOTE}/run.sh </dev/null >/dev/null 2>&1 & sleep 1")


def verify():
    checks = [("/api/catalog", 200), ("/api/examples", 200), ("/api/projects", 200),
              ("/", 200), ("/api/project/DEMO-RUN-001/cost", 200),
              ("/api/project/DEMO-RUN-001/model.glb", 200)]
    for attempt in range(30):
        try:
            urllib.request.urlopen(BASE + "/api/catalog", timeout=5)
            break
        except Exception:
            time.sleep(2)
    fails = 0
    for p, want in checks:
        try:
            with urllib.request.urlopen(BASE + p, timeout=30) as r:
                got = r.status
        except Exception as ex:
            got = getattr(ex, "code", str(ex)[:50])
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'OK ' if ok else '?? '} {p} → {got}")
    if fails:
        sys.exit(f"검증 실패 {fails}건")
    print(f"검증 통과: {BASE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--meshes", action="store_true", help="원본 파트 메시까지 동기화")
    ap.add_argument("--verify", action="store_true", help="확인만 한다")
    a = ap.parse_args()
    if not a.verify:
        deploy(a.meshes)
    verify()
