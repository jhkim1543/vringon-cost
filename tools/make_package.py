# -*- coding: utf-8 -*-
"""전달용 패키지(zip)를 만든다.

들어가는 것
  코드          server/ web/ tools/ tests/ deploy/ 실행 파일과 문서
  원가 정보     워크북에서 온 씨앗 데이터 + 사람이 읽을 수 있는 CSV 로 변환
  데모 결과물   프로젝트별 상태·원가 JSON, 뷰어 GLB, 입력 이미지, 원가 라인 CSV
  안내          링크와 읽는 법을 적은 README

들어가지 않는 것
  키와 공급자 주소(.provider.json), .venv, .git, docs/(정적 빌드 산출물),
  무거운 원본 메시(segmented/completed/raw, 프로젝트당 120MB)

마지막에 스테이지 전체를 훑어 비밀이 섞였는지 확인하고, 하나라도 나오면 멈춘다.

    python tools/make_package.py
"""
import csv
import io
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import catalog  # noqa: E402

OUT_DIR = ROOT / "deploy"
STAGE = OUT_DIR / "package-stage"

CODE_ITEMS = ["server", "web", "tools", "tests", ".platform",
              "deploy/eb_bundle.py", "deploy/eb_deploy.py",
              "deploy/gpu_deploy.py", "deploy/h100-run.sh"]
DOC_FILES = ["HANDOFF.md", "QA-원가검증.md", "개선계획.md", "HOWTO-업로드.md",
             "README.md", "requirements-eb.txt", "Procfile", "run.cmd",
             ".gitignore"]
PROJ_FILES = ("state.json", "cost.json", "viewer.glb", "model_mapping.json")

DEMO_LINKS = {
    "실서버 데모 (업로드·3D 생성·재계산 전부 동작)": "http://61.107.200.148:18452/",
    "3D 없이 여는 화면 (WebGL 차단 환경 재현)":
        "http://61.107.200.148:18452/?p=DEMO-RUN-001&no3d=1",
    "정적 데모 (백엔드 없이 열람)": "https://jhkim1543.github.io/vringon-cost/",
    "소스 저장소": "https://github.com/jhkim1543/vringon-cost",
}


def w_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(header)
        wr.writerows(rows)


def val(v):
    if isinstance(v, dict) and "value" in v:
        return v["value"], v.get("source", "")
    return v, ""


def export_prices(dst):
    rows = []
    for (q, spec), r in sorted(catalog.quarterly_prices().items()):
        rows.append([q, spec, r.get("description"), r.get("uom"),
                     r.get("p10"), r.get("p50"), r.get("p90"),
                     r.get("price_basis"), r.get("eligibility"),
                     r.get("confidence"), r.get("valid_from"), r.get("valid_to"),
                     r.get("source_url"), r.get("note")])
    w_csv(dst / "단가표_분기스냅샷.csv",
          ["분기", "소재ID", "소재설명", "구매단위", "낮음", "기준", "높음",
           "단가근거", "원가자격", "신뢰", "유효시작", "유효종료", "출처URL", "비고"],
          rows)
    return len(rows)


def export_part_material_map(dst):
    rows = [[r.get("part_group"), r.get("part"), r.get("material"), r.get("spec"),
             r.get("uom"), r.get("usd_low"), r.get("usd_high"), r.get("tier"),
             r.get("confidence"), r.get("driver"), r.get("source"), r.get("url")]
            for r in catalog.part_material_map()]
    w_csv(dst / "파트별_소재후보와_가격범위.csv",
          ["파트그룹", "세부파트", "주요소재", "대표사양", "구매단위",
           "USD하한", "USD상한", "가격층", "신뢰", "핵심 Cost Driver",
           "업데이트 소스", "원본 URL"], rows)
    return len(rows)


def export_material_specs(dst):
    rows = []
    for spec, d in sorted(catalog.material_specs().items()):
        for k, v in d.items():
            if k in ("form", "description") or k.startswith("_"):
                continue
            value, src = val(v)
            if isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            note = v.get("note") if isinstance(v, dict) else ""
            rows.append([spec, d.get("description"), d.get("form"), k, value,
                         src, note or ""])
    w_csv(dst / "소재_공학파라미터와_출처.csv",
          ["소재ID", "설명", "형태", "파라미터", "값", "출처등급", "비고"], rows)
    return len(rows)


def export_rules(dst):
    rows = [[r["rule_id"], r["construction"], r["condition"], r["add_part"],
             r.get("material_role"), r.get("qty_method"), r.get("raw_parameters"),
             r.get("priority"), r.get("evidence"), r.get("approval_role")]
            for r in catalog.recipes()]
    w_csv(dst / "구성규칙_숨은파트.csv",
          ["규칙ID", "구성", "조건식", "추가 파트", "소재 역할", "소요량 산식",
           "기본 파라미터", "우선순위", "근거", "승인 역할"], rows)

    bm = catalog.bom_master()
    rows2 = [[r["part_id"], r["assembly"], r["canonical_part"], r.get("visibility"),
              r.get("segmentation_expected"), r.get("qty_basis"),
              r.get("geometry_metric"), r.get("uom"), r.get("formula_family"),
              r.get("priority"), r.get("description")]
             for r in sorted(bm.values(), key=lambda x: x["part_id"])]
    w_csv(dst / "BOM마스터_파트정의.csv",
          ["파트ID", "조립군", "Canonical Part", "가시성", "세그멘테이션 기대",
           "수량 기준", "지오메트리 지표", "단위", "산식군", "우선순위", "설명"], rows2)

    rows3 = [[o.get("op_id"), o.get("seq"), o.get("operation"), o.get("workcenter"),
              o.get("sam_min"), o.get("line_efficiency"), o.get("labor_rate_usd_hr"),
              o.get("machine_min"), o.get("machine_rate_usd_hr"),
              o.get("data_status"), o.get("note")]
             for o in catalog.routing()]
    w_csv(dst / "공정라우팅_현재_전부_TBD.csv",
          ["공정ID", "순서", "공정", "작업장", "SAM(분)", "라인효율",
           "노무단가(USD/h)", "기계시간(분)", "기계단가(USD/h)", "데이터상태", "비고"],
          rows3)

    rows4 = [[t.get("tool_id"), t.get("tool_type"), t.get("tool_cost_usd"),
              t.get("tool_life_pairs"), t.get("allocation_qty"), t.get("note")]
             for t in catalog.tooling()]
    w_csv(dst / "금형마스터_견적_미입력.csv",
          ["금형ID", "종류", "견적(USD)", "수명(켤레)", "배부수량", "비고"], rows4)
    return len(rows) + len(rows2) + len(rows3) + len(rows4)


def export_project_lines(pid, cost, dst):
    rows = []
    for l in cost["lines"]:
        c, p = l["consumption"], l["price"]
        g = l.get("geometry") or {}
        vol = g.get("volume_m3")
        rows.append([
            l["line_id"], l.get("canonical_part"), l.get("assembly"),
            l.get("origin"), l.get("rule_id") or "", l.get("material_spec") or "",
            g.get("surface_area_m2"), (vol * 1e6) if vol else "",
            g.get("method"), c.get("gross_qty"), c.get("uom"),
            p.get("p10"), p.get("p50"), p.get("p90"), p.get("basis"),
            p.get("eligibility"),
            l.get("cost_p10"), l.get("cost_p50"), l.get("cost_p90"),
            l.get("status"), l.get("max_class"),
            " | ".join(l.get("blocked") or []),
            " | ".join(l.get("warnings") or []),
        ])
    w_csv(dst / (pid + "_원가라인.csv"),
          ["라인ID", "파트", "조립군", "출처", "규칙ID", "소재ID",
           "면적(m2/짝)", "부피(cm3/짝)", "측정방법", "소요량", "단위",
           "단가낮음", "단가기준", "단가높음", "단가근거", "원가자격",
           "금액낮음", "금액기준", "금액높음", "상태", "등급상한",
           "차단사유", "경고"], rows)


def scan_secrets(stage):
    """키·공급자 주소·공급사명이 섞였는지 훑는다."""
    bad = []
    key = host = None
    terms = []
    try:
        from config import provider_api_key, PROVIDER_BASE, banned_terms
        key, host = provider_api_key(), PROVIDER_BASE
        terms = banned_terms()
    except Exception:
        pass
    pats = [re.compile(r"[A-Za-z0-9_-]*API_KEY\s*=\s*\S+")]
    text_ext = (".py", ".js", ".json", ".md", ".csv", ".txt", ".html",
                ".css", ".cmd", ".sh", ".conf", "")
    for f in stage.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(stage)
        low_name = f.name.lower()
        if any(t in low_name for t in terms):
            bad.append("파일명: " + str(rel))
        if f.suffix.lower() not in text_ext:
            continue
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if key and len(key) > 8 and key in t:
            bad.append("키: " + str(rel))
        if host and len(host) > 8 and host in t:
            bad.append("공급자 주소: " + str(rel))
        low = t.lower()
        if any(x in low for x in terms):
            bad.append("공급사명: " + str(rel))
        for p in pats:
            if p.search(t):
                bad.append("키 패턴: " + str(rel))
                break
    return bad


def readme(n_proj, n_price, n_map, n_spec, n_rule):
    links = "\n".join("- {}\n  {}".format(k, v) for k, v in DEMO_LINKS.items())
    return """# VRINGON Cost — 전달 패키지

신발 디자인 이미지에서 부품 구조와 초기 제조 BOM 을 제안하고, 소요량과 단가를
추적 가능한 방식으로 조합해 **개념 단계 Should-Cost** 를 계산하는 데모입니다.

## 이 숫자가 무엇이고 무엇이 아닌가

화면과 CSV 의 금액은 **완성 신발 원가가 아닙니다.** 공개 단가와 3D 추정
소요량으로 계산한 **일부 재료·포장비 소계**입니다. 노무·기계·금형은 공장
데이터가 없어 차단(Blocked)되어 있고, 그래서 FOB 와 전체 제조원가는 계산되지
않습니다. 데이터가 없을 때 0 으로 채우지 않고 차단으로 세우는 것이 이 도구의
핵심 설계입니다.

- 유효: 디자인 초기에 원가 영향이 큰 부품 찾기, 대안 비교, RFQ 준비
- 불가: 실제 양산원가·FOB 산출, 바이어·공장 제출용 공식 코스팅 시트

실제 공개 코스트시트와의 차이를 항목별로 이은 분석과 아직 필요한 데이터
목록은 `code/QA-원가검증.md` 에 있습니다.

## 데모 링크

{links}

정적 데모는 미리 계산된 결과를 읽기 전용으로 봅니다. 실서버 데모는 업로드부터
3D 생성, 재계산까지 전부 동작합니다.

## 폴더 구성

```
README.md              이 문서
code/                  전체 소스
  server/              계산 엔진 (BOM, 소요량, 단가, 원가, 기하)
  web/                 화면
  tools/               감사·빌드·패키징 도구
  tests/               회귀 테스트
  deploy/              배포 스크립트
  HANDOFF.md           정본 인수인계 문서
  QA-원가검증.md        원가 검증 보고서 (실측 대조, 결함 수정 이력)
원가정보/               계산에 실제로 쓰이는 데이터
  단가표_분기스냅샷.csv           {n_price}행, 출처 URL 포함
  파트별_소재후보와_가격범위.csv   {n_map}행
  소재_공학파라미터와_출처.csv     {n_spec}행, 값마다 출처등급 표시
  구성규칙_숨은파트.csv           3D 에 안 보이는 파트를 넣는 규칙
  BOM마스터_파트정의.csv          파트 정의
  공정라우팅_현재_전부_TBD.csv    노무·기계가 왜 차단인지 보여줌
  금형마스터_견적_미입력.csv       금형이 왜 차단인지 보여줌
  원본_워크북_추출/               위 CSV 의 원본 JSON
데모결과/               프로젝트 {n_proj}개
  프로젝트_요약.csv               한 장 요약
  <프로젝트>/
    <프로젝트>_원가라인.csv       라인별 소요량·단가·금액·차단사유
    cost.json                    계산 결과 원본
    state.json                   파이프라인 상태 (매핑·캘리브레이션 포함)
    viewer.glb                   3D 결과 (파트 분리된 경량 메시)
    입력이미지_*                  생성에 쓴 원본 이미지
```

## 출처 등급 읽는 법

`소재_공학파라미터와_출처.csv` 의 출처등급은 그 값이 어디서 왔는지 말합니다.

- `workbook` 첨부 워크북에 있던 값
- `public_reference` 공개 자료에서 가져온 값
- `assumption` 이 데모가 세운 가정
- `factory_required` 공장 확인이 필요한 값. 있으면 C2 등급으로 못 올라갑니다

단가의 `원가자격` 도 같은 역할을 합니다. `Concept only` 는 공개 리스팅이라
개념 단계에서만 쓸 수 있다는 뜻입니다.

## 숫자 표기

낮음·기준·높음은 단가 범위를 그대로 대입한 **시나리오 값**입니다. 분포를
모델링한 통계 백분위가 아니고 라인 간 상관도 반영하지 않았으므로, 합계 구간은
실제보다 넓게 나올 수 있습니다.

## 직접 돌려보려면

```
python -m venv .venv
.venv\\Scripts\\pip install -r code/requirements-eb.txt
.venv\\Scripts\\python code/server/app.py
```

3D 생성 엔진 실호출에는 별도 키가 필요하며 이 패키지에 없습니다
(환경변수 `MESH_API_KEY`, `MESH_API_BASE`). 키 없이도 이미 계산된
{n_proj}개 프로젝트는 전부 열람·재계산됩니다.

회귀 테스트: `.venv\\Scripts\\python -m pytest code/tests -q`
원가 감사:   `.venv\\Scripts\\python code/tools/audit_costing.py`
""".format(links=links, n_proj=n_proj, n_price=n_price, n_map=n_map,
           n_spec=n_spec, n_rule=n_rule)


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    code = STAGE / "code"
    for item in CODE_ITEMS:
        src = ROOT / item
        if not src.exists():
            print("  빠짐:", item)
            continue
        dst = code / item
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                          "package-stage",
                                                          "eb-stage", "*.zip"))
        else:
            shutil.copy2(src, dst)
    for f in DOC_FILES:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, code / f)

    money = STAGE / "원가정보"
    money.mkdir()
    shutil.copytree(ROOT / "data" / "seed", money / "원본_워크북_추출")
    shutil.copy2(ROOT / "data" / "material_specs.json",
                 money / "원본_워크북_추출" / "material_specs.json")
    n_price = export_prices(money)
    n_map = export_part_material_map(money)
    n_spec = export_material_specs(money)
    n_rule = export_rules(money)

    demo = STAGE / "데모결과"
    demo.mkdir()
    summary = []
    for proj in sorted((ROOT / "data" / "projects").iterdir()):
        cost_f = proj / "cost.json"
        if not cost_f.exists():
            continue
        pdir = demo / proj.name
        pdir.mkdir()
        for name in PROJ_FILES:
            if (proj / name).exists():
                shutil.copy2(proj / name, pdir / name)
        cost = json.loads(cost_f.read_text(encoding="utf-8"))
        export_project_lines(proj.name, cost, pdir)
        state = json.loads((proj / "state.json").read_text(encoding="utf-8"))
        img = state.get("input_image")
        if img and (ROOT / "data" / "assets" / img).exists():
            shutil.copy2(ROOT / "data" / "assets" / img, pdir / ("입력이미지_" + img))
        r = cost["rollup"]
        mb = cost.get("mass_balance") or {}
        summary.append([
            proj.name, cost["scenario"].get("quarter"),
            r["known_cost_subtotal"]["p10"], r["known_cost_subtotal"]["p50"],
            r["known_cost_subtotal"]["p90"],
            r["coverage"]["priced_lines"], r["coverage"]["bom_lines"],
            r["cost_status"], cost["grade"]["class"],
            ", ".join(r.get("blocked_buckets") or []),
            r.get("manufacturing_should_cost"), r.get("fob"),
            round(mb.get("finished_pair_mass_g") or 0, 1), mb.get("verdict"),
            len(r.get("sanity_warnings") or []),
        ])
        print("  {:16} ${:.3f}  {}/{}라인".format(
            proj.name, r["known_cost_subtotal"]["p50"],
            r["coverage"]["priced_lines"], r["coverage"]["bom_lines"]))

    w_csv(demo / "프로젝트_요약.csv",
          ["프로젝트", "분기", "소계낮음", "소계기준", "소계높음",
           "가격확정라인", "전체라인", "원가상태", "등급", "차단버킷",
           "제조원가", "FOB", "완제품질량(g/켤레)", "질량판정", "경고수"],
          summary)

    (STAGE / "README.md").write_text(
        readme(len(summary), n_price, n_map, n_spec, n_rule), encoding="utf-8")

    leaks = scan_secrets(STAGE)
    if leaks:
        for x in leaks[:10]:
            print("  누출:", x)
        sys.exit("중단: 비밀로 보이는 내용 {}건".format(len(leaks)))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    zpath = OUT_DIR / ("vringon-cost_패키지_" + stamp + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(STAGE).as_posix())
    print("\n{}  {:.1f} MB  (프로젝트 {}개, 단가 {}행, 소재 파라미터 {}행)".format(
        zpath.name, zpath.stat().st_size / 1e6, len(summary), n_price, n_spec))
    return zpath


if __name__ == "__main__":
    main()
