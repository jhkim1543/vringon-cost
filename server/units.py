# -*- coding: utf-8 -*-
"""차원 안전성 (dimensional type safety).

폴리백이 켤레당 1kg 으로 잡혔던 사고는 개별 실수가 아니라 데이터 모델에
차원 검사가 없다는 신호였다. 여기서 UOM 을 차원으로 환원해 다음을 막는다.

    count 와 USD/kg 의 곱
    m2 와 USD/piece 의 곱
    kg 와 USD/m 의 곱

또한 수량 기준(per_shoe, per_pair, per_batch)을 명시적으로 변환한다.
모든 라인에 2를 곱하는 방식은 틀린다. 레이스와 박스는 이미 켤레 단위다.
"""

# UOM 을 (차원, 정규표기) 로 환원한다.
_UOM = {
    "m": ("length", "m"), "linear m": ("length", "m"), "yard": ("length", "yd"),
    "m²": ("area", "m²"), "m2": ("area", "m²"), "sq ft": ("area", "sq ft"),
    "m³": ("volume", "m³"), "m3": ("volume", "m³"),
    "kg": ("mass", "kg"), "g": ("mass", "g"),
    "piece": ("count", "piece"), "pcs": ("count", "piece"), "ea": ("count", "piece"),
    "pair": ("count", "pair"), "sheet": ("count", "sheet"),
    "roll": ("count", "roll"),
}

# 한 짝 기준 값을 켤레로 올릴 때 쓰는 배수.
BASIS_TO_PAIR = {
    "per_shoe": 2.0,
    "per_pair": 1.0,
    "per_model": 2.0,
    "per_batch": None,
    "per_dozen": None,
}

# 계산식별 허용 차원 signature.
FORMULA_SIGNATURE = {
    "roll":     {"input": "area",   "output": "length"},
    "sheet":    {"input": "area",   "output": "count"},
    "sheet_m2": {"input": "area",   "output": "area"},
    "molded":   {"input": "volume", "output": "mass"},
    "chemical": {"input": "area",   "output": "mass"},
    "thread":   {"input": "length", "output": "mass"},
    "length":   {"input": "length", "output": "length"},
    "count":    {"input": "count",  "output": "count"},
    "mass":     {"input": "count",  "output": "mass"},
}


def dimension(uom):
    """UOM 의 차원. 모르면 None 을 준다. 조용히 통과시키지 않기 위해서다."""
    if uom is None:
        return None
    d = _UOM.get(str(uom).strip())
    return d[0] if d else None


def normalize(uom):
    d = _UOM.get(str(uom).strip()) if uom is not None else None
    return d[1] if d else uom


# 같은 차원이라도 단위가 다르면 그냥 곱하면 안 된다. 면적을 m² 로 재고
# 단가가 USD/sq ft 면 10.76 배 틀린다. 차원만 보던 예전 검사는 이 구멍을
# 열어 두고 있었다(가죽이 sq ft 로 팔리므로 실제로 물릴 자리였다).
# 여기서 단위를 명시적으로 환산하고, 환산 계수를 계산 근거에 남긴다.
_TO_BASE = {
    "m": 1.0, "yd": 0.9144,                      # length -> m
    "m²": 1.0, "sq ft": 0.09290304,              # area -> m²
    "m³": 1.0,                                   # volume -> m³
    "kg": 1.0, "g": 0.001,                       # mass -> kg
}


def convert_factor(from_uom, to_uom):
    """from_uom 으로 잰 값을 to_uom 기준으로 바꾸는 계수. 모르면 None."""
    a, b = normalize(from_uom), normalize(to_uom)
    if a == b:
        return 1.0
    fa, fb = _TO_BASE.get(a), _TO_BASE.get(b)
    if fa is None or fb is None or fb == 0:
        return None
    return fa / fb


def check_multiply(qty_uom, price_uom):
    """수량과 단가를 곱해도 되는지 본다.

    반환 (ok, 사유, 단가환산계수). 모르는 UOM 은 통과시키지 않는다.
    단가환산계수는 '단가를 수량 단위 기준으로 바꾸는 수'다. 예를 들어
    수량이 m² 이고 단가가 USD/sq ft 면 1 m² 당 단가는 USD/sq ft x 10.7639 이다.
    """
    qd, pd = dimension(qty_uom), dimension(price_uom)
    if qd is None:
        return False, f"알 수 없는 소요량 단위 '{qty_uom}'", None
    if pd is None:
        return False, f"알 수 없는 단가 단위 '{price_uom}'", None
    if qd != pd:
        return False, (f"차원 불일치. 소요량 {normalize(qty_uom)}({qd}) 와 "
                       f"단가 {normalize(price_uom)}({pd})"), None
    a, b = normalize(qty_uom), normalize(price_uom)
    # 개수 계열은 환산이라는 것이 없다. 정확히 같아야 한다.
    if qd == "count":
        if a != b and {a, b} != {"piece", "pair"}:
            return False, f"수량 단위 불일치. {a} 와 {b}", None
        return True, None, 1.0
    if a == b:
        return True, None, 1.0
    # 단가 1 단위(b)가 수량 단위(a) 로 몇 개인지 = 단가에 곱할 수
    f = convert_factor(a, b)
    if f is None:
        return False, f"단위 환산 계수를 모른다. {a} 와 {b}", None
    return True, f"단가 환산 {b} -> {a} 계수 {f:.6g}", f


def check_formula(form, qty_uom, geometry_unit=None):
    """계산식이 선언한 signature 대로 나왔는지 확인한다."""
    sig = FORMULA_SIGNATURE.get(form)
    if not sig:
        return False, f"알 수 없는 계산식 '{form}'"
    out = dimension(qty_uom)
    if out != sig["output"]:
        return False, (f"'{form}' 의 출력 차원은 {sig['output']} 이어야 하는데 "
                       f"{out}({qty_uom}) 가 나왔다")
    return True, None


def to_pair(value, basis, batch_qty=None):
    """한 짝 또는 배치 기준 수량을 켤레 기준으로 바꾼다.

    반환 (값, 배수, 사유). 환산할 수 없으면 값이 None 이다.
    """
    if value is None:
        return None, None, "수량 없음"
    f = BASIS_TO_PAIR.get(basis)
    if f is None:
        if basis == "per_batch" and batch_qty:
            return value / float(batch_qty), 1.0 / float(batch_qty), "배치를 켤레로 환산"
        return None, None, f"수량 기준 '{basis}' 를 켤레로 환산할 수 없다"
    return value * f, f, None
