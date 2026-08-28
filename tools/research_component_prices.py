# -*- coding: utf-8 -*-
"""부품 층위 단가를 웹 검색 LLM 두 종으로 조사한다 (A0 Estimated 조사치).

왜: 우리 소재비의 최대 격차 요인은 "원료 kg 단가 대 성형·가공 부품 단가"다.
승인 견적(A2)이 없는 동안, 출처 URL 이 붙은 조사치로 benchmark_bridge 의
하드코딩 가정을 대체한다. 조사치는 A0 이며 원가 계산의 단가를 바꾸지 않는다.

정직성 규칙:
  - 두 모델이 따로 조사하고, 결과를 병합할 때 겹치는 항목만 신뢰도를 올린다.
  - URL 이 없는 숫자는 버린다.
  - 결과는 data/benchmarks/component_research.json 에만 저장한다.

    python tools/research_component_prices.py [--skip-openai] [--skip-gemini]

키는 이 리포에 없다. ../blueocean-agent/.env 에서 읽는다.
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
OUT = ROOT / "data" / "benchmarks" / "component_research.json"

ITEMS = [
    ("midsole_unit", "EVA compression molded midsole unit for running shoes, "
                     "price per pair from footwear component suppliers"),
    ("outsole_unit", "rubber outsole unit price per pair, footwear component "
                     "supplier wholesale"),
    ("engineered_mesh", "engineered knit mesh fabric for shoe uppers, price "
                        "per yard or per meter, bulk supplier"),
    ("sockliner_unit", "EVA or PU sockliner insole unit price per pair, bulk"),
    ("shoe_box", "custom printed shoe box unit price, bulk order 5000 units"),
]

PROMPT = """You are researching wholesale component prices for athletic footwear
manufacturing (China/Vietnam supply chain, 2025-2026). For each item below,
find real supplier listings or trade sources and report a price range in USD.

Items:
{items}

Rules:
- Only report numbers you found at a URL. Include that URL for every number.
- Report ranges (low/high) per unit stated. If sources conflict, keep the range wide.
- These are listings, not negotiated quotes. Do not present them as actual costs.

Return STRICT JSON only, no prose:
{{"items": [{{"key": "...", "usd_low": 0.0, "usd_high": 0.0, "unit": "pair|yard|m|piece",
  "sources": ["https://..."], "note": "..."}}]}}
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
    """웹 검색이 켜진 응답 API. 오래 걸릴 수 있어 background 로 돌리고 기다린다."""
    base = "https://api.openai.com/v1"
    h = {"Authorization": f"Bearer {key}"}
    job = post_json(f"{base}/responses", {
        "model": "gpt-5.5", "input": prompt,
        "tools": [{"type": "web_search"}], "background": True,
    }, h)
    rid = job["id"]
    for _ in range(60):                       # 최대 10분
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


def merge(results):
    """모델별 결과를 항목 단위로 병합한다. 둘 다 찾은 항목은 corroborated."""
    by_key = {}
    for engine, data in results.items():
        for it in (data or {}).get("items", []):
            key = it.get("key")
            lo, hi = it.get("usd_low"), it.get("usd_high")
            srcs = [s for s in (it.get("sources") or [])
                    if isinstance(s, str) and s.startswith("http")]
            if not key or lo is None or hi is None or not srcs:
                continue                        # URL 없는 숫자는 버린다
            e = by_key.setdefault(key, {"key": key, "engines": [],
                                        "ranges": [], "sources": [],
                                        "unit": it.get("unit"),
                                        "notes": []})
            e["engines"].append(engine)
            e["ranges"].append((float(lo), float(hi)))
            e["sources"] += srcs
            if it.get("note"):
                e["notes"].append(f"{engine}: {it['note']}")
    out = []
    for key, e in by_key.items():
        lows = [r[0] for r in e["ranges"]]
        highs = [r[1] for r in e["ranges"]]
        out.append({
            "key": key,
            "usd_low": min(lows), "usd_high": max(highs),
            "unit": e["unit"],
            "corroborated": len(set(e["engines"])) >= 2,
            "engines": sorted(set(e["engines"])),
            "sources": sorted(set(e["sources"]))[:8],
            "notes": e["notes"][:4],
        })
    return out


def main():
    keys = env_keys()
    items_txt = "\n".join(f"- {k}: {q}" for k, q in ITEMS)
    prompt = PROMPT.format(items=items_txt)
    results = {}
    if "--skip-openai" not in sys.argv and keys.get("OPENAI_API_KEY"):
        try:
            print("검색엔진 A 조사 중 (수 분)")
            results["engine_a"] = run_openai(keys["OPENAI_API_KEY"], prompt)
            print("  항목", len((results['engine_a'] or {}).get('items', [])))
        except Exception as e:
            print("  실패:", str(e)[:160])
    if "--skip-gemini" not in sys.argv and keys.get("GEMINI_API_KEY"):
        try:
            print("검색엔진 B 조사 중")
            results["engine_b"] = run_gemini(keys["GEMINI_API_KEY"], prompt)
            print("  항목", len((results['engine_b'] or {}).get('items', [])))
        except Exception as e:
            print("  실패:", str(e)[:160])

    merged = merge(results)
    out = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": "A0",
        "tier_note": ("웹 검색 조사치. 리스팅이지 협상가가 아니며 원가 계산의 "
                      "단가를 바꾸지 않는다. benchmark_bridge 의 대조 가정에만 쓴다."),
        "method": "독립 웹검색 LLM 2종 병합. URL 없는 숫자는 버림. "
                  "두 엔진이 모두 찾은 항목만 corroborated.",
        "items": merged,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n병합 {len(merged)}항목 -> {OUT.name}")
    for it in merged:
        c = "상호검증" if it["corroborated"] else "단일출처"
        print(f"  {it['key']:16} ${it['usd_low']}~{it['usd_high']}/{it['unit']}"
              f"  {c}  출처 {len(it['sources'])}개")
    return out


if __name__ == "__main__":
    main()
