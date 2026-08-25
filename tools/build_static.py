# -*- coding: utf-8 -*-
"""정적 배포본(docs/) 생성.

    python tools/build_static.py [project_id ...]

이 데모는 파이썬 백엔드(trimesh 기하 연산 + 생성 엔진 호출)에 의존한다.
GitHub Pages 에는 백엔드가 없으므로, 계산 결과를 JSON 으로 구워두고
fetch('/api/...') 를 그 파일들로 가로챈다. 결과는 읽기 전용 데모다.

정적 모드에서 빠지는 것: 3D 생성 엔진 신규 생성, 재계산(시나리오 변경), 매핑 확정,
메시 복구. 화면에 그렇다고 표시한다.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import catalog                      # noqa: E402
import canonical                    # noqa: E402
from pipeline import Project        # noqa: E402

DOCS = ROOT / "docs"
WEB = ROOT / "web"


def build_catalog():
    return {
        "canonical_parts": sorted(catalog.bom_master()),
        "signature_parts": sorted(canonical.SIGNATURES),
        "bom_master": catalog.bom_master(),
        "material_specs": catalog.material_specs(),
        "part_defaults": catalog.part_defaults(),
        "recipes": catalog.recipes(),
        "routing": catalog.routing(),
        "tooling": catalog.tooling(),
        "quarters": sorted({q for (q, _s) in catalog.quarterly_prices()}),
        "part_material_map": catalog.part_material_map(),
        "mesh_provider_key_present": False,
        "static_mode": True,
    }


def main():
    pids = sys.argv[1:] or [d.name for d in (ROOT / "data" / "projects").iterdir()
                            if (d / "state.json").exists()]
    if not pids:
        sys.exit("배포할 프로젝트가 없습니다.")

    if DOCS.exists():
        shutil.rmtree(DOCS)
    (DOCS / "data").mkdir(parents=True)

    # 1) 프런트엔드 그대로 복사
    for item in WEB.iterdir():
        dst = DOCS / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy2(item, dst)

    # 2) 계산 결과 굽기
    (DOCS / "data" / "catalog.json").write_text(
        json.dumps(build_catalog(), ensure_ascii=False), encoding="utf-8")

    built = []
    for pid in pids:
        p = Project(pid)
        if not p.state_path.exists():
            print(f"  skip  {pid} (state.json 없음)")
            continue
        (DOCS / "data" / f"project_{pid}.json").write_text(
            json.dumps(p.state, ensure_ascii=False), encoding="utf-8")

        cost = p.dir / "cost.json"
        if cost.exists():
            shutil.copy2(cost, DOCS / "data" / f"cost_{pid}.json")

        # 뷰어 GLB 가 이미 있으면 그대로 쓴다. 원본 파트 메시가 없는
        # 프로젝트(서버에서 생성된 것)도 정적 데모에는 실을 수 있어야 한다.
        try:
            glb = p.viewer_glb()
        except Exception as e:
            glb = p.dir / "viewer.glb"
            if not glb.exists():
                print(f"  skip  {pid} ({str(e)[:50]})")
                continue
            print(f"  note  {pid} 기존 viewer.glb 사용")
        shutil.copy2(glb, DOCS / "data" / f"{pid}.glb")

        img = p.state.get("input_image")
        if img:
            src = ROOT / "data" / "assets" / img
            if src.exists():
                shutil.copy2(src, DOCS / "data" / f"{pid}{src.suffix}")
        size = (DOCS / "data" / f"{pid}.glb").stat().st_size / 1048576
        print(f"  ok    {pid}  glb {size:.1f} MB"
              f"  cost {'있음' if cost.exists() else '없음'}")
        built.append(pid)

    (DOCS / "data" / "index.json").write_text(
        json.dumps({"projects": built, "default": built[0]}, ensure_ascii=False),
        encoding="utf-8")

    # 3) 정적 모드 shim 을 app.js 앞에 끼운다
    idx = DOCS / "index.html"
    html = idx.read_text(encoding="utf-8")
    # web/index.html 이 이미 shim 을 싣고 있으면 다시 넣지 않는다.
    if "static-api.js" not in html:
        html = html.replace(
            '<script type="module" src="app.js"></script>',
            '<script src="static-api.js"></script>\n'
            '<script type="module" src="app.js"></script>')
    # 정적 빌드 표시. 이게 없으면 shim 은 매니페스트를 찾지 않는다
    # (실서버에서 data/index.json 을 찔러 404 가 찍히던 것을 없앤다).
    if "__staticBuild" not in html:
        html = html.replace(
            '<script src="static-api.js"></script>',
            '<script>window.__staticBuild=true;</script>' + chr(10) +
            '<script src="static-api.js"></script>')
    # 백엔드 주소가 있으면 구워 넣는다. shim 이 부팅 때 살아있는지 확인하고,
    # 살아있으면 실서버 모드로, 죽어있으면 지금처럼 정적 모드로 동작한다.
    backend = ROOT / "deploy" / "backend.json"
    if backend.exists() and "__backendBase" not in html:
        base = json.loads(backend.read_text(encoding="utf-8"))["base"].rstrip("/")
        html = html.replace(
            '<script src="static-api.js"></script>',
            f'<script>window.__backendBase="{base}";</script>\n'
            '<script src="static-api.js"></script>')
    idx.write_text(html, encoding="utf-8")

    # 예시 디자인 목록과 이미지
    ex_src = ROOT / "data" / "examples"
    if (ex_src / "examples.json").exists():
        exd = json.loads((ex_src / "examples.json").read_text(encoding="utf-8"))
        for ex in exd["examples"]:
            ex["ready"] = (ROOT / "data" / "projects" / ex["project"] / "cost.json").exists()
            img = ex_src / ex["image"]
            if img.exists():
                shutil.copy2(img, DOCS / "data" / ("ex_" + ex["image"]))
        (DOCS / "data" / "examples.json").write_text(
            json.dumps(exd, ensure_ascii=False), encoding="utf-8")
        print(f"  예시 {len(exd['examples'])}건")

    # Jekyll 이 _ 로 시작하는 경로를 먹지 않도록
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    total = sum(f.stat().st_size for f in DOCS.rglob("*") if f.is_file()) / 1048576
    print(f"\n{len(built)}개 프로젝트, docs/ 총 {total:.1f} MB")


if __name__ == "__main__":
    main()
