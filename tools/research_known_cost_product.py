# -*- coding: utf-8 -*-
"""실제 원가가 공개된 신발 제품을 웹 검색 LLM 2종으로 찾는다.

목적: 우리 파이프라인에 그 제품의 이미지를 넣고, 공개된 실제 원가와
우리 계산을 같은 버킷끼리 대조하기 위한 정답지 수집.

찾는 것: 브랜드가 스스로 공개한(투명 가격제) 또는 신뢰할 만한 취재로
항목별 제조원가가 공개된 스니커. 항목별 금액과 출처 URL, 제품 측면
이미지 URL 까지.

    python tools/research_known_cost_product.py
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT.parent / "blueocean-agent" / ".env"
OUT = ROOT / "data" / "benchmarks" / "known_cost_products.json"

PROMPT = """Find sneakers/shoes whose REAL production cost breakdown has been
publicly disclosed - either by the brand itself (transparent pricing, e.g.
brands that publish "true cost" per product) or by credible reporting with
itemized numbers.

For each product report:
- brand, product name, retail price USD
- the itemized cost breakdown EXACTLY as disclosed (item name + USD amount),
  e.g. materials, hardware, labor, duties, transport
- which items are MATERIALS (vs labor/duty/transport)
- construction/upper material (leather? knit? mesh?)
- a direct URL to a side-view product image (jpg/png/webp) if findable
- source URLs for every number

Focus on: Everlane Tread trainer, Oliver Cabell Low 1 / Phoenix, Italic,
Asket, or any other brand with published per-product cost breakdowns.
2-4 products is enough. Numbers must come from the cited pages.

Return STRICT JSON only:
{"products":[{"brand":"...","name":"...","retail_usd":0,
 "cost_lines":[{"item":"...","usd":0.0,"is_material":true}],
 "total_disclosed_usd":0.0,"materials_usd":0.0,
 "upper_material":"leather|knit|mesh|canvas",
 "image_url":"https://... or null","sources":["https://..."]}]}
"""


def env_keys():
    keys = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(OPENAI_API_KEY|GEMINI_API_KEY)=(.+)", line.strip())
        if m:
            keys[m.group(1)] = m.group(2).strip()
    return keys


def post_json(url, body, headers, timeout=120):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(url, headers, timeout=60):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def extract_json(text):
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def run_openai(key, prompt):
    base = "https://api.openai.com/v1"
    h = {"Authorization": f"Bearer {key}"}
    job = post_json(f"{base}/responses", {
        "model": "gpt-5.5", "input": prompt,
        "tools": [{"type": "web_search"}], "background": True,
    }, h)
    rid = job["id"]
    st = {}
    for _ in range(72):                       # 최대 12분
        time.sleep(10)
        st = get_json(f"{base}/responses/{rid}", h)
        if st.get("status") in ("completed", "failed", "cancelled", "incomplete"):
            break
    if st.get("status") != "completed":
        raise RuntimeError(f"responses {st.get('status')}")
    texts = []
    for item in st.get("output", []):
        for c in item.get("content", []) or []:
            if c.get("type") == "output_text":
                texts.append(c.get("text", ""))
    return extract_json("\n".join(texts))


def run_gemini(key, prompt):
    base = "https://generativelanguage.googleapis.com/v1beta/models"
    for model in ("gemini-3.1-pro-preview", "gemini-3.6-flash"):
        try:
            r = post_json(
                f"{base}/{model}:generateContent?key={key}",
                {"contents": [{"parts": [{"text": prompt}]}],
                 "tools": [{"google_search": {}}]},
                {}, timeout=300)
            parts = r["candidates"][0]["content"]["parts"]
            text = "\n".join(p.get("text", "") for p in parts)
            out = extract_json(text)
            if out:
                return out
        except Exception as e:
            print(f"  {model}: {str(e)[:100]}")
    return None


def valid(p):
    return (p.get("brand") and p.get("cost_lines")
            and any(s.startswith("http") for s in (p.get("sources") or []))
            and p.get("materials_usd"))


def main():
    keys = env_keys()
    results = {}
    try:
        print("검색엔진 A 조사 중 (수 분)")
        results["engine_a"] = run_openai(keys["OPENAI_API_KEY"], PROMPT)
        print("  제품", len((results['engine_a'] or {}).get('products', [])))
    except Exception as e:
        print("  실패:", str(e)[:160])
    try:
        print("검색엔진 B 조사 중")
        results["engine_b"] = run_gemini(keys["GEMINI_API_KEY"], PROMPT)
        print("  제품", len((results['engine_b'] or {}).get('products', [])))
    except Exception as e:
        print("  실패:", str(e)[:160])

    # 브랜드+제품명으로 병합. 두 엔진이 모두 찾은 제품은 corroborated,
    # 금액이 다르면 둘 다 남긴다 (조사 불일치 자체가 정보다).
    by_key = {}
    for engine, data in results.items():
        for p in (data or {}).get("products", []):
            if not valid(p):
                continue
            k = (p.get("brand", "").strip().lower(),
                 p.get("name", "").strip().lower())
            e = by_key.setdefault(k, {"reports": []})
            e["reports"].append({"engine": engine, **p})
    products = []
    for k, e in by_key.items():
        engines = sorted({r["engine"] for r in e["reports"]})
        products.append({
            "brand": e["reports"][0]["brand"], "name": e["reports"][0]["name"],
            "corroborated": len(engines) >= 2, "engines": engines,
            "reports": e["reports"],
        })

    out = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": "A1",
        "note": "브랜드 공개 또는 취재 기반 실제 원가. 우리 계산의 정답지 대조용.",
        "products": products,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n{len(products)}개 제품 -> {OUT.name}")
    for p in products:
        r0 = p["reports"][0]
        print(f"  {p['brand']} {p['name']}: 소재비 ${r0.get('materials_usd')}"
              f" / 공개원가 ${r0.get('total_disclosed_usd')}"
              f"  {'상호검증' if p['corroborated'] else '단일'}"
              f"  이미지 {'있음' if r0.get('image_url') else '없음'}")
    return out


if __name__ == "__main__":
    main()
