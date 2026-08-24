# -*- coding: utf-8 -*-
"""Elastic Beanstalk 번들을 만든다 → deploy/eb-bundle.zip

들어가는 것: server/  web/  Procfile  .platform/  requirements-eb.txt(→requirements.txt)
데이터는 런타임에 필요한 것만 추린다. 프로젝트별 무거운 원본 GLB(원본·세그·복원,
각 40MB 안팎)는 빼고 viewer.glb 와 상태·결과·이미지·매핑만 넣는다. 씨앗(seed)과
소재 스펙, 예시 목록은 전부 들어간다.

절대 넣지 않는 것: .provider.json (공급자 호스트, 환경변수 MESH_API_BASE 로 주입),
키 파일, .venv, docs, 이름에 공급자명이 들어간 파일.

    python deploy/eb_bundle.py
"""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "deploy" / "eb-stage"
ZIP = ROOT / "deploy" / "eb-bundle.zip"

# 공급자명이 파일명에 남은 과거 산출물(작업 기록 json)은 번들에 들어가면 안 된다
BANNED_NAME_PARTS = ("tripo",)

PROJECT_KEEP = ("state.json", "cost.json", "viewer.glb", "model_mapping.json")
PROJECT_KEEP_EXT = (".jpg", ".jpeg", ".png", ".webp")


def keep_project_file(p: Path) -> bool:
    n = p.name.lower()
    if any(b in n for b in BANNED_NAME_PARTS):
        return False
    return p.name in PROJECT_KEEP or p.suffix.lower() in PROJECT_KEEP_EXT


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    shutil.copytree(ROOT / "server", STAGE / "server",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "web", STAGE / "web")
    shutil.copy2(ROOT / "Procfile", STAGE / "Procfile")
    shutil.copy2(ROOT / "requirements-eb.txt", STAGE / "requirements.txt")
    shutil.copytree(ROOT / ".platform", STAGE / ".platform")

    # 비밀이 스테이지에 들어오지 않았는지 확인 (있으면 멈춘다)
    for bad in [STAGE / ".provider.json", STAGE / "server" / ".provider.json"]:
        if bad.exists():
            sys.exit(f"중단: 비밀 파일이 번들에 들어왔다 {bad}")

    data = STAGE / "data"
    (data / "projects").mkdir(parents=True)
    shutil.copy2(ROOT / "data" / "material_specs.json", data / "material_specs.json")
    shutil.copytree(ROOT / "data" / "seed", data / "seed")
    shutil.copytree(ROOT / "data" / "examples", data / "examples")
    shutil.copytree(ROOT / "data" / "assets", data / "assets")  # 입력 이미지

    for proj in sorted((ROOT / "data" / "projects").iterdir()):
        if not (proj / "cost.json").exists():
            continue  # 미완성 프로젝트는 씨앗으로 넣지 않는다
        dst = data / "projects" / proj.name
        dst.mkdir()
        n = 0
        for f in proj.iterdir():
            if f.is_file() and keep_project_file(f):
                shutil.copy2(f, dst / f.name)
                n += 1
        print(f"  {proj.name}: 파일 {n}개")

    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(STAGE).as_posix())

    mb = ZIP.stat().st_size / 1e6
    print(f"번들 {ZIP.name}  {mb:.1f} MB")
    if mb > 400:
        sys.exit("중단: 번들이 400MB 를 넘는다")


if __name__ == "__main__":
    main()
