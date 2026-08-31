# -*- coding: utf-8 -*-
"""임포트 경계 테스트 — vringon-ai-workers 의 tests/architecture 패턴.

경계가 깨지면 코드는 돌지만 구조가 조용히 무너진다. 그래서 테스트로 지킨다.

  1. 엔진(평면 모듈)은 HTTP 를 모른다: fastapi, modules, core 를 임포트하지 않는다.
  2. 도메인 라우터끼리는 서로 임포트하지 않는다 (BE 도메인 모듈 규칙).
  3. core 는 modules 를 임포트하지 않는다.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

ENGINES = ["geometry.py", "bom.py", "consumption.py", "pricing.py",
           "costing.py", "units.py", "catalog.py", "canonical.py",
           "measures.py", "repair.py", "pipeline.py", "mesh_provider.py",
           "config.py"]

_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_.]+)", re.M)


def imports_of(path):
    return set(_IMPORT.findall(path.read_text(encoding="utf-8")))


def test_engines_do_not_know_http():
    """엔진은 fastapi·라우터·core(API 공통층)를 임포트하지 못한다."""
    for name in ENGINES:
        mods = imports_of(SERVER / name)
        bad = [m for m in mods
               if m.split(".")[0] in ("fastapi", "modules", "core", "uvicorn",
                                      "starlette")]
        assert not bad, f"{name} 이 API 계층을 임포트한다: {bad}"


def test_routers_do_not_import_each_other():
    """도메인 라우터는 서로를 모른다. 공유가 필요하면 엔진이나 core 로 내린다."""
    routers = sorted((SERVER / "modules").glob("*_api.py"))
    assert len(routers) >= 8, "도메인 라우터가 사라졌다"
    names = {r.stem for r in routers}
    for r in routers:
        mods = imports_of(r)
        cross = [m for m in mods
                 if m.split(".")[-1] in names and m.split(".")[-1] != r.stem]
        cross += [m for m in mods
                  if m.startswith("modules") and r.stem not in m]
        assert not cross, f"{r.name} 이 다른 라우터를 임포트한다: {cross}"


def test_core_does_not_import_modules():
    for f in (SERVER / "core").glob("*.py"):
        mods = imports_of(f)
        bad = [m for m in mods if m.split(".")[0] == "modules"]
        assert not bad, f"core/{f.name} 이 도메인 라우터를 임포트한다: {bad}"


def test_starter_is_thin():
    """진입 모듈은 조립만 한다. 엔진 임포트가 나타나면 로직이 새고 있는 것."""
    mods = imports_of(SERVER / "app.py")
    engine_names = {Path(e).stem for e in ENGINES} - {"config"}
    bad = [m for m in mods if m.split(".")[0] in engine_names]
    assert not bad, f"app.py 가 엔진을 직접 임포트한다: {bad}"


def test_error_codes_are_registered():
    """라우터가 쓰는 VC-* 코드는 등록부에 있어야 한다."""
    from core.errors import CODES
    used = set()
    for r in (SERVER / "modules").glob("*_api.py"):
        used |= set(re.findall(r"VC-[A-Z]+-\d{3}", r.read_text(encoding="utf-8")))
    unknown = used - set(CODES)
    assert not unknown, f"등록되지 않은 오류 코드: {unknown}"
