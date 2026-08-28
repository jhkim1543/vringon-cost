# -*- coding: utf-8 -*-
"""공개 시장 지수를 가져와 분기 스냅샷과 대조한다 (A1 Benchmark 파이프라인).

원칙 (외부 검토 2026-08-28):
  - 지수는 특정 SKU 의 구매가가 아니다. 변동성 추적과 이상치 검사에만 쓴다.
  - 결과는 A1 (Benchmark) 로만 저장하며 원가 계산의 단가를 바꾸지 않는다.
  - 지수와 스냅샷의 괴리가 크면 "다음 분기 스냅샷을 갱신하라" 는 신호다.

출처 (무료 공개 API, 키 불필요):
  - World Bank Commodity Markets (Pink Sheet) 월간 xlsx: 천연고무 RSS3·TSR20, 면화 A
  - ECB 환율 API: USD/EUR, KRW/EUR (참고용 reference rate, 결제 환율 아님)

    python tools/fetch_benchmarks.py
"""
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

OUT_DIR = ROOT / "data" / "benchmarks"

# Pink Sheet 파일 주소는 매월 문서 해시가 바뀐다. 랜딩 페이지에서 최신
# 링크를 찾고, 실패하면 마지막으로 확인된 주소로 물러난다.
PINK_LANDING = "https://www.worldbank.org/en/research/commodity-markets"
PINK_FALLBACK = ("https://thedocs.worldbank.org/en/doc/"
                 "74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/"
                 "CMO-Historical-Data-Monthly.xlsx")


def pink_url():
    import re
    try:
        html = fetch(PINK_LANDING, timeout=30).decode("utf-8", "ignore")
        m = re.search(r"https://[^\"']*CMO-Historical-Data-Monthly\.xlsx", html)
        if m:
            return m.group(0)
    except Exception:
        pass
    return PINK_FALLBACK
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/M.{cur}.EUR.SP00.A?lastNObservations=3&format=csvdata"

# Pink Sheet 열 이름 -> 우리 스냅샷의 어떤 소재와 대조할 것인가.
# 지수는 원료(습고무·원면)이고 스냅샷은 배합·가공된 소재라 절대값 비교가
# 아니라 방향·변동률 비교다.
COMMODITY_MAP = {
    "Rubber, RSS3": {"unit": "USD/kg", "compare_specs": ["MAT-NR"]},
    "Rubber, TSR20": {"unit": "USD/kg", "compare_specs": ["MAT-SBR", "MAT-BR", "MAT-NBR"]},
    "Cotton, A Index": {"unit": "USD/kg", "compare_specs": []},
}


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "vringon-cost/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pink_sheet():
    """월간 Pink Sheet 에서 대상 상품의 최근 시계열을 뽑는다."""
    import openpyxl
    raw = fetch(pink_url(), timeout=120)
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb["Monthly Prices"]
    rows = list(ws.iter_rows(values_only=True))
    # 헤더 행: 상품 이름이 들어 있는 첫 행을 찾는다 (레이아웃 방어)
    header_i, header = None, None
    for i, row in enumerate(rows[:12]):
        cells = [str(c) if c is not None else "" for c in row]
        if any("RSS3" in c for c in cells):
            header_i, header = i, cells
            break
    if header is None:
        raise RuntimeError("Pink Sheet 레이아웃이 바뀌었다. RSS3 열을 못 찾음")
    cols = {}
    for name in COMMODITY_MAP:
        for j, h in enumerate(header):
            if name.lower().replace(" ", "") in h.lower().replace(" ", ""):
                cols[name] = j
                break
    out = {}
    for name, j in cols.items():
        series = []
        for row in rows[header_i + 1:]:
            date, val = row[0], row[j] if j < len(row) else None
            if date is None or val is None:
                continue
            try:
                series.append((str(date), float(val)))
            except (TypeError, ValueError):
                continue
        if series:
            out[name] = {"unit": COMMODITY_MAP[name]["unit"],
                         "recent": series[-6:],
                         "latest": {"period": series[-1][0],
                                    "value": series[-1][1]}}
    return out


def ecb_fx():
    out = {}
    for cur in ("USD", "KRW"):
        try:
            txt = fetch(ECB.format(cur=cur), timeout=30).decode("utf-8")
            lines = [l.split(",") for l in txt.strip().splitlines()[1:]]
            hdr = txt.splitlines()[0].split(",")
            ti, vi = hdr.index("TIME_PERIOD"), hdr.index("OBS_VALUE")
            series = [(l[ti], float(l[vi])) for l in lines if l[vi]]
            out[f"{cur}_per_EUR"] = {"recent": series,
                                     "latest": {"period": series[-1][0],
                                                "value": series[-1][1]}}
        except Exception as e:
            out[f"{cur}_per_EUR"] = {"error": str(e)[:120]}
    u = out.get("USD_per_EUR", {}).get("latest", {}).get("value")
    k = out.get("KRW_per_EUR", {}).get("latest", {}).get("value")
    if u and k:
        out["KRW_per_USD"] = {"latest": {"period": out["USD_per_EUR"]["latest"]["period"],
                                         "value": k / u}}
    out["_note"] = ("ECB reference rate. 정보 제공용이며 결제 환율이 아니다. "
                    "실제 원가 확정에는 송금·정산 환율이 필요하다.")
    return out


def compare_with_snapshot(commodities):
    """A1(시장지수 기반) 스냅샷 소재와 지수의 분기 내 변동을 대조한다."""
    import catalog
    quarter = "2026Q3"
    rows = []
    for name, info in commodities.items():
        seq = info.get("recent") or []
        recent = dict(seq)
        latest = info.get("latest", {})
        # 분기 시작(2026M07) 기준 변동률. 그 달이 없으면 최근 3개월 변동으로.
        start = recent.get("2026M07")
        basis = "분기 시작(2026M07) 대비"
        if start is None and len(seq) >= 4:
            start = seq[-4][1]
            basis = f"3개월 전({seq[-4][0]}) 대비"
        drift = ((latest.get("value") - start) / start * 100) if start else None
        for spec in COMMODITY_MAP[name]["compare_specs"]:
            snap = catalog.quarterly_prices().get((quarter, spec))
            if not snap:
                continue
            rows.append({
                "index_name": name,
                "index_latest": latest,
                "index_unit": info["unit"],
                "quarter_start_value": start,
                "drift_basis": basis,
                "drift_pct": round(drift, 2) if drift is not None else None,
                "index_data_current": str(latest.get("period", "")) >= "2026M07",
                "compare_spec": spec,
                "snapshot_p50": snap["p50"],
                "snapshot_uom": snap["uom"],
                "note": ("지수는 원료 가격이고 스냅샷은 배합·가공 소재다. "
                         "절대값이 아니라 방향·변동률만 본다. 변동률이 ±10% 를 "
                         "넘으면 다음 분기 스냅샷 갱신이 필요하다는 신호다."),
                "action_needed": drift is not None and abs(drift) > 10,
            })
    return rows


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": "A1",
        "tier_note": "시장 지수. 특정 SKU 구매가가 아니며 원가 단가를 바꾸지 않는다.",
        "sources": {
            "commodities": "World Bank Commodity Markets (Pink Sheet), 월간",
            "fx": "ECB reference exchange rates",
        },
        "commodities": {},
        "fx": {},
        "snapshot_comparison": [],
        "errors": [],
    }
    try:
        result["commodities"] = pink_sheet()
    except Exception as e:
        result["errors"].append(f"pink_sheet: {str(e)[:200]}")
    try:
        result["fx"] = ecb_fx()
    except Exception as e:
        result["errors"].append(f"ecb: {str(e)[:200]}")
    if result["commodities"]:
        try:
            result["snapshot_comparison"] = compare_with_snapshot(result["commodities"])
        except Exception as e:
            result["errors"].append(f"compare: {str(e)[:200]}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m")
    (OUT_DIR / f"benchmarks_{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"수집 {len(result['commodities'])}개 상품, 오류 {len(result['errors'])}건")
    for name, info in result["commodities"].items():
        l = info["latest"]
        print(f"  {name:18} {l['value']:.3f} {info['unit']}  ({l['period']})")
    for r in result["snapshot_comparison"]:
        flag = " <- 스냅샷 갱신 신호" if r["action_needed"] else ""
        fresh = "" if r.get("index_data_current") else " (지수 데이터가 스냅샷 분기보다 오래됨)"
        print(f"  {r['compare_spec']:10} 스냅샷 {r['snapshot_p50']} | "
              f"{r['drift_basis']} {r['drift_pct']}%{flag}{fresh}")
    for e in result["errors"]:
        print("  오류:", e)
    return result


if __name__ == "__main__":
    main()
