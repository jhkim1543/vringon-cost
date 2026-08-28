# -*- coding: utf-8 -*-
"""실제 원가가 공개된 제품을 파이프라인에 넣고 같은 버킷끼리 대조한다.

    python tools/run_known_cost_test.py <이미지경로> <프로젝트ID> [--leather]
    python tools/run_known_cost_test.py --compare <프로젝트ID> <공개소재비USD> [라벨]

1단계(실행): 라이브 서버에 이미지를 올려 생성 → 세그멘테이션 → 복구(완성 포함)
→ 매핑 확정 → (선택) 갑피 가죽 지정 → BOM → 원가. 크레딧 약 120 소모.
2단계(대조): 우리 전체 소재비(승인+미승인)와 공개 소재비를 비교하고,
부품 단가 층위 조정(benchmark_bridge 방식) 후 잔차를 분해한다.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
BASE = "http://61.107.200.148:18452/api"

LEATHER_PARTS = {"Vamp": "MAT-FULLGRAIN", "Heel Overlay": "MAT-FULLGRAIN",
                 "Mudguard/Toe Overlay": "MAT-FULLGRAIN",
                 "Collar Shell": "MAT-FULLGRAIN"}


def req(method, path, body=None, files=None, timeout=180):
    url = BASE + path
    if files:
        import mimetypes, uuid
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (body or {}).items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                         f"name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        for k, (fn, data) in files.items():
            ct = mimetypes.guess_type(fn)[0] or "application/octet-stream"
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                         f"name=\"{k}\"; filename=\"{fn}\"\r\n"
                         f"Content-Type: {ct}\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        r = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, method=method, headers={
            "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def run(image_path, pid, leather=False):
    img = Path(image_path)
    print(f"업로드 {img.name} -> {pid}")
    req("POST", "/mesh/generate", {"project_id": pid, "segment": "true"},
        files={"image": (img.name, img.read_bytes())})
    for i in range(90):                       # 최대 30분
        time.sleep(20)
        j = req("GET", f"/mesh/job/{pid}")
        print(f"  {j.get('stage')} {j.get('progress')}%")
        if j.get("stage") == "done":
            break
        if j.get("stage") == "error":
            sys.exit("생성 실패: " + str(j.get("error")))
    print("스케일 확정 (외부 길이 300mm 가정)")
    req("POST", f"/project/{pid}/landmarks", {}, timeout=300)
    req("POST", f"/project/{pid}/calibrate",
        {"target_length_mm": 300, "confirmed": True}, timeout=300)
    print("복구 (완성본 포함, 수 분)")
    req("POST", f"/project/{pid}/repair", {}, timeout=600)
    req("POST", f"/project/{pid}/segment/confirm", {"confirm_all": True},
        timeout=300)
    if leather:
        st = req("GET", f"/project/{pid}")
        parts = {m["canonical_part"] for m in st.get("mapping", [])}
        sel = {k: v for k, v in LEATHER_PARTS.items() if k in parts}
        if sel:
            print("가죽 지정:", ", ".join(sel))
            req("POST", f"/project/{pid}/materials", sel)
    req("POST", f"/project/{pid}/bom", {}, timeout=600)
    d = req("POST", f"/project/{pid}/cost", {}, timeout=600)
    ru = d["rollup"]
    total = (ru["known_cost_subtotal"]["p50"]
             + ru["unapproved_material_subtotal"]["p50"])
    print(f"\n승인 ${ru['known_cost_subtotal']['p50']:.3f} + "
          f"미승인 ${ru['unapproved_material_subtotal']['p50']:.3f} "
          f"= 전체 소재비 ${total:.3f}")
    return d


def compare(pid, disclosed_materials_usd, label=""):
    """전체 소재비 대 공개 소재비. 부품 단가 조정 후 잔차 분해."""
    f = ROOT / "data" / "projects" / pid / "cost.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    ru = d["rollup"]
    ours = (ru["known_cost_subtotal"]["p50"]
            + (ru.get("unapproved_material_subtotal") or {}).get("p50", 0.0))
    lines = d["lines"]

    # 부품 단가 층위 조정 (benchmark_bridge 와 동일 논리, 조사치 사용)
    rf = ROOT / "data" / "benchmarks" / "component_research.json"
    research = {}
    if rf.exists():
        research = {it["key"]: it for it in
                    json.loads(rf.read_text(encoding="utf-8"))["items"]}

    def mid(key, default):
        it = research.get(key)
        return (it["usd_low"] + it["usd_high"]) / 2 if it else default

    unit_price = {"Midsole Carrier": mid("midsole_unit", 2.0),
                  "Outsole Rubber": mid("outsole_unit", 1.5),
                  "Midsole Insert": mid("sockliner_unit", 0.6)}
    molded_delta = sum(unit_price[l["canonical_part"]] - l["cost_p50"]
                       for l in lines
                       if l["canonical_part"] in unit_price and l.get("cost_p50"))
    UPPER_SPECS = {"MAT-MESH-POLY", "MAT-MESH-3D", "MAT-MICROFIBER"}
    upper_delta = sum(l["cost_p50"] * 3.0 for l in lines
                      if l.get("material_spec") in UPPER_SPECS
                      and l.get("cost_p50"))
    adjusted = ours + molded_delta + upper_delta

    gap_raw = (ours - disclosed_materials_usd) / disclosed_materials_usd * 100
    gap_adj = (adjusted - disclosed_materials_usd) / disclosed_materials_usd * 100
    print(f"\n== {pid}  대  {label or '공개 소재비'} ${disclosed_materials_usd:.2f} ==")
    print(f"우리 전체 소재비 (원료 단가)      ${ours:7.2f}   차이 {gap_raw:+.0f}%")
    print(f"  성형 부품을 유닛 단가로        {molded_delta:+7.2f}")
    print(f"  갑피 원단을 실구매 단가로      {upper_delta:+7.2f}")
    print(f"부품 단가 조정 후               ${adjusted:7.2f}   차이 {gap_adj:+.0f}%")
    top = sorted((l for l in lines if l.get("cost_p50")),
                 key=lambda l: -l["cost_p50"])[:6]
    print("우리 상위 라인:")
    for l in top:
        print(f"  {l['canonical_part']:22} ${l['cost_p50']:.3f}"
              f"  ({l.get('material_spec')})")
    return {"ours_raw": ours, "ours_adjusted": adjusted,
            "disclosed": disclosed_materials_usd,
            "gap_raw_pct": gap_raw, "gap_adjusted_pct": gap_adj}


if __name__ == "__main__":
    if sys.argv[1] == "--compare":
        compare(sys.argv[2], float(sys.argv[3]),
                sys.argv[4] if len(sys.argv) > 4 else "")
    else:
        run(sys.argv[1], sys.argv[2], leather="--leather" in sys.argv)
