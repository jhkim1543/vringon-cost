# -*- coding: utf-8 -*-
"""공개된 실제 원가와의 차이를 항목별로 잇는다.

우리 값은 소재를 **원료 단가**로 계산한 C1 컨셉 값이다. 공개 코스트시트는
**부품 구매가** 기준이다. 둘을 바로 빼면 "얼마나 틀렸나"만 남고 "왜 다른가"가
사라진다. 그래서 조정 항목을 하나씩 쌓아 올려 다리를 놓는다.

조정값은 전부 공개 자료와 업계 통상 범위에서 온 **가정**이다. 공장 견적이
아니므로 이 다리의 결과는 검증이지 원가가 아니다.

    python tools/benchmark_bridge.py [프로젝트ID]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 기준: Shoemakers Academy (Wade Motawi) $70 소매 스니커 코스트시트
# FOB $15.00 = 어퍼 34% + 가죽 16% + 아웃솔 14% + LOP 27% + 포장 6% + 금형 3%
BENCH = {
    "name": "$70 소매 스니커 (Shoemakers Academy 코스트시트)",
    "fob": 15.00,
    "upper": 5.10, "leather": 2.40, "outsole": 2.10,
    "lop": 4.05, "packing": 0.90, "mold": 0.45,
}
BENCH_MAT = BENCH["upper"] + BENCH["leather"] + BENCH["outsole"]   # 9.60

# 성형 부품은 원료 kg 단가가 아니라 성형·트리밍된 유닛 단가로 산다.
# 기본값은 공개 리스팅 통상값. tools/research_component_prices.py 를 돌리면
# 출처 URL 이 붙은 조사치(data/benchmarks/component_research.json)로 대체된다.
MOLDED_UNIT_USD_PAIR = {
    "Midsole Carrier": (1.50, 2.50),
    "Outsole Rubber": (1.00, 2.00),
    "Midsole Insert": (0.40, 0.80),
}
RESEARCH_FILE = ROOT / "data" / "benchmarks" / "component_research.json"
RESEARCH_KEY_MAP = {"midsole_unit": "Midsole Carrier",
                    "outsole_unit": "Outsole Rubber",
                    "sockliner_unit": "Midsole Insert"}


def load_research():
    """조사치가 있으면 다리의 가정을 그것으로 바꾼다. 출처 개수를 알려준다."""
    if not RESEARCH_FILE.exists():
        return None
    d = json.loads(RESEARCH_FILE.read_text(encoding="utf-8"))
    used = {}
    for it in d.get("items", []):
        part = RESEARCH_KEY_MAP.get(it.get("key"))
        if part and it.get("usd_low") is not None:
            MOLDED_UNIT_USD_PAIR[part] = (it["usd_low"], it["usd_high"])
            used[part] = {"sources": len(it.get("sources") or []),
                          "corroborated": it.get("corroborated")}
    return {"fetched_at": d.get("fetched_at"), "used": used}
# 어퍼 원단: 워크북의 $0.80/m 는 범용 메시 하한이다. 러닝화용 engineered mesh
# 실구매는 야드당 $3~5 (= m 당 약 $3.3~5.5) 이므로 4배로 본다.
UPPER_FABRIC_FACTOR = 4.0
UPPER_SPECS = {"MAT-MESH-POLY", "MAT-MESH-3D", "MAT-MICROFIBER"}

# 규칙에는 있으나 이 디자인에서 라인이 안 나온 필수 부자재의 통상 단가
# 깔창·봉제사는 규칙을 추가해 이제 계산에 들어간다. 다만 우리 값은 원료
# 단가라서 부품 구매가와 차이가 남는다 (깔창 유닛 $0.30~0.80 대 원료 $0.14).
MISSING_USD_PAIR = {
    "깔창을 원료에서 성형 유닛 단가로": 0.35,
    "Eyelet/Lace Loop (RFQ 대기)": 0.20,
    "Hardener (RFQ 대기)": 0.05,
    "Labels/Hangtag (소재 스펙 필요)": 0.15,
    "Master Carton 분담 (스펙 필요)": 0.10,
}
# 신발 상자: 우리 $1.15 는 소매 고급 박스급이다. 기준 코스트시트의 포장 전체가
# $0.90 이므로 상자만 $1.15 는 과대하다. 통상 벌크 박스로 되돌린다.
BOX_REAL = 0.50


def bridge(pid):
    f = ROOT / "data" / "projects" / pid / "cost.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    lines = d["lines"]
    # 벤치마크는 전체 소재비이므로 우리도 전체(승인+미승인)로 비교한다.
    # 승인 소계만 쓰면 미승인 숨은 파트가 빠져 비교가 왜곡된다.
    ru = d["rollup"]
    base = (ru["known_cost_subtotal"]["p50"]
            + (ru.get("unapproved_material_subtotal") or {}).get("p50", 0.0))

    steps = []
    # 1) 성형 부품을 부품 단가로
    molded_delta = 0.0
    for l in lines:
        cp = l["canonical_part"]
        if cp in MOLDED_UNIT_USD_PAIR and l.get("cost_p50"):
            lo, hi = MOLDED_UNIT_USD_PAIR[cp]
            molded_delta += (lo + hi) / 2 - l["cost_p50"]
    steps.append(("성형 부품을 원료 kg 에서 부품 유닛 단가로", molded_delta))

    # 2) 어퍼 원단을 실구매 단가로
    upper_delta = sum(l["cost_p50"] * (UPPER_FABRIC_FACTOR - 1)
                      for l in lines
                      if l.get("material_spec") in UPPER_SPECS and l.get("cost_p50"))
    steps.append(("어퍼 원단을 리스팅 하한에서 실구매 단가로", upper_delta))

    # 3) 빠진 필수 부자재
    steps.append((f"규칙에 있으나 안 잡힌 필수 부자재 {len(MISSING_USD_PAIR)}건",
                  sum(MISSING_USD_PAIR.values())))

    # 4) 상자 과대 보정
    box = next((l["cost_p50"] for l in lines
                if l["canonical_part"] == "Shoe Box" and l.get("cost_p50")), None)
    if box:
        steps.append(("신발 상자를 벌크 단가로 되돌림", BOX_REAL - box))

    print(f"\n== {pid} ==")
    print(f"{'우리 계산 전체 소재비 (원료 단가, 승인+미승인)':42} {base:8.2f}")
    run = base
    for label, delta in steps:
        run += delta
        print(f"{('  ' + label):46} {delta:+8.2f}  -> {run:6.2f}")
    print(f"{'조정 후':46} {run:8.2f}")
    print(f"{'기준 실측 소재비 ' + BENCH['name'][:26]:46} {BENCH_MAT:8.2f}")
    gap = run - BENCH_MAT
    print(f"{'남은 차이':46} {gap:+8.2f}  ({gap/BENCH_MAT*100:+.0f}%)")
    return base, run


if __name__ == "__main__":
    pids = sys.argv[1:] or ["DEMO-RUN-001"]
    print(f"기준: {BENCH['name']}  FOB ${BENCH['fob']:.2f}, 소재 합 ${BENCH_MAT:.2f}")
    research = load_research()
    if research and research["used"]:
        parts = ", ".join(f"{k}(출처 {v['sources']}개"
                          f"{', 상호검증' if v['corroborated'] else ''})"
                          for k, v in research["used"].items())
        print(f"부품 단가: 웹 조사치 사용 ({research['fetched_at'][:10]}) - {parts}")
    else:
        print("조정값은 공개 자료 기반 가정이다. 공장 견적이 아니다.")
    for pid in pids:
        bridge(pid)
