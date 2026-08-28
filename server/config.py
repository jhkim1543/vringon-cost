# -*- coding: utf-8 -*-
"""경로·상수·환경 설정. 비밀키는 코드에 두지 않는다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEED = DATA / "seed"
STORE = DATA / "projects"      # 프로젝트별 산출물(GLB, 계산 결과)
ASSETS = DATA / "assets"       # 업로드 원본 이미지 (Object Storage 대역)
WEB = ROOT / "web"

for _p in (STORE, ASSETS):
    _p.mkdir(parents=True, exist_ok=True)

# ── 3D 생성 엔진 ────────────────────────────────────────────────────────────────
# v2 는 2026-11-01 종료. 신규는 v3 만 사용한다.
def _provider_base():
    """생성 엔진 API base. 리포에 호스트를 커밋하지 않는다.

    우선순위: 환경변수 MESH_API_BASE > 리포 루트의 .provider.json (git 제외).
    없으면 실호출 기능만 비활성화되고 나머지 데모는 동작한다.
    """
    v = os.environ.get("MESH_API_BASE")
    if v:
        return v.rstrip("/")
    f = ROOT / ".provider.json"
    if f.exists():
        import json as _json
        try:
            return _json.loads(f.read_text(encoding="utf-8"))["base"].rstrip("/")
        except Exception:
            pass
    return None


PROVIDER_BASE = _provider_base()
# 2026-08-24 기준 서버가 허용하는 model 값:
#   P1-20260311, P2-20260801, v2.5-20250123, v3.0-20250812, v3.1-20260211
# 신발은 캐릭터 특화(P계열)가 아닌 범용 최신 라인을 쓴다.
PROVIDER_MODEL = os.environ.get("PROVIDER_MODEL", "v3.1-20260211")
# 생성 결과 URL은 5분만 유효 -> 성공 즉시 내려받는다.
PROVIDER_URL_TTL_SEC = 300


def provider_api_key():
    """환경변수 우선, 없으면 세션 루트의 run_backend.cmd 에서 회수.

    리포에 키를 커밋하지 않기 위한 로컬 개발용 폴백이다.
    """
    key = os.environ.get("MESH_API_KEY")
    if key:
        return key.strip()
    # 폴백 파일의 변수명은 우리가 통제하지 않는다. 이름에 의존하지 말고
    # 어떤 *_API_KEY 줄이든 값을 집는다.
    import re
    fallback = ROOT.parent / "scripts" / "run_backend.cmd"
    if fallback.exists():
        for line in fallback.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(r"[A-Z_]*API_KEY\s*=\s*(\S+)", line)
            if m:
                return m.group(1).strip()
    return None


# ── 원가 등급 게이트 ───────────────────────────────────────────────────────
# 계획서 §2. 상위 등급일수록 요구 입력이 많다.
COST_CLASSES = ["C0", "C1", "C2", "C3", "C4"]

CLASS_REQUIREMENTS = {
    "C1": [
        ("metric_calibrated", "사용자 확인된 toe–heel 길이로 metric scale 확정"),
        ("segmented", "파트 세그멘테이션 결과 존재"),
        ("construction_set", "construction 유형 선택"),
        ("mbom_built", "rule 기반 manufacturing BOM 생성"),
    ],
    "C2": [
        ("volume_parts_validated", "부피 계산 파트가 watertight QA 통과"),
        ("hidden_bom_approved", "Hidden BOM 엔지니어 승인"),
        ("pattern_approved", "승인 pattern/solid geometry (DXF 또는 sole CAD)"),
        ("supplier_price", "승인 Supplier SKU 견적"),
        ("routing_confirmed", "공장 routing·SAM 확정"),
        ("tooling_confirmed", "Tooling 견적 확정"),
    ],
}

# 가격 우선순위 (계획서 §12.2). 낮은 rank 가 우선.
PRICE_BASIS_RANK = {
    "approved_supplier_quote": 1,
    "escalated_historical_quote": 2,
    "comparable_supplier_quote": 3,
    "quarterly_snapshot": 4,
    "public_listing": 5,
}

# Cost Eligibility 문자열 -> 허용 최고 등급
ELIGIBILITY_MAX_CLASS = {
    "Engineering": "C2",
    "Concept benchmark": "C1",
    "Concept only": "C1",
}

def banned_terms():
    """배포물에 나가면 안 되는 단어. 리포에 적지 않고 로컬 설정에서 읽는다.

    공급사명은 화면·저장소·문서 어디에도 남기지 않기로 했으므로, 그것을
    검사하는 목록 자체도 리포에 두지 않는다. 설정이 없으면 빈 목록이라
    검사가 느슨해지지만, 대신 키와 공급자 주소 검사는 그대로 동작한다.
    """
    import json
    out = []
    f = ROOT / ".provider.json"
    if f.exists():
        try:
            out = [str(t).lower() for t in
                   (json.loads(f.read_text(encoding="utf-8")).get("banned_terms") or [])]
        except Exception:
            out = []
    env = os.environ.get("BANNED_TERMS", "")
    out += [t.strip().lower() for t in env.split(",") if t.strip()]
    return [t for t in out if len(t) >= 4]
