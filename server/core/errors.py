# -*- coding: utf-8 -*-
"""오류 코드 체계 — VRINGON-BE 의 VO-<도메인>-NNN 규약을 따른 VC-* 등록부.

BE 는 도메인별 ErrorCode enum(HttpStatus + code + message)을 두고
GlobalExceptionHandler 가 ErrorResponse 로 바꾼다. 여기서는 같은 계약을
파이썬으로 옮긴다: 라우터는 CostError 를 던지거나 err() 로 감싸고,
응답 본문은 {code, message} (개발 모드에서는 trace 포함) 이다.

엔진(평면 모듈)은 이 파일을 임포트하지 않는다. 엔진은 HTTP 를 모른 채
ValueError·FileNotFoundError 를 던지고, 라우터가 도메인 코드를 붙인다.
일시 오류와 영구 실패의 구분 원칙은 BE 의 VO-TASK-002 주석을 따른다.
"""
import os
import traceback

from fastapi.responses import JSONResponse

# 코드 등록부. 새 코드는 여기와 ARCHITECTURE.md 에 함께 적는다.
CODES = {
    "VC-PROJ-001": "프로젝트 ID 형식 위반",
    "VC-PROJ-002": "아직 계산되지 않음",
    "VC-GEO-001": "파트 메시 없음 (세그멘테이션 필요)",
    "VC-GEO-002": "이 배포본에는 원본 메시가 없음 (열람만 가능)",
    "VC-GEO-003": "캘리브레이션 없음",
    "VC-MAT-001": "알 수 없는 소재",
    "VC-MAT-002": "해당 분기 단가 없는 소재",
    "VC-BOM-001": "BOM 없음",
    "VC-BOM-002": "승인자·근거 누락",
    "VC-GATE-001": "알 수 없는 게이트",
    "VC-GATE-002": "게이트 승인 증거 누락",
    "VC-GEN-001": "지원하지 않는 이미지 형식",
    "VC-GEN-002": "이미지 크기 초과",
    "VC-GEN-003": "생성 엔진 키 없음",
    "VC-COMMON-000": "분류되지 않은 오류",
}


class CostError(Exception):
    """도메인 코드가 붙은 오류. BE 의 VringonException 에 해당."""

    def __init__(self, code, message=None, status=400):
        self.code = code if code in CODES else "VC-COMMON-000"
        self.message = message or CODES.get(self.code, str(code))
        self.status = status
        super().__init__(self.message)


def dev_mode():
    """EB 는 PORT 를 준다. 운영에서는 내부 정보를 내보내지 않는다."""
    return os.environ.get("ALLOW_DEBUG") == "1" or not os.environ.get("PORT")


def err(e, code="VC-COMMON-000", status=400):
    """예외 -> 오류 응답. CostError 면 제 코드를, 아니면 라우터가 준 코드를 쓴다.

    'error' 키는 기존 화면(j.error)과의 호환을 위해 유지한다.
    """
    if isinstance(e, CostError):
        code, status = e.code, e.status
        msg = e.message
    else:
        msg = str(e)
    body = {"code": code, "message": msg, "error": msg}
    if dev_mode():
        body["trace"] = traceback.format_exc()[-1200:]
    return JSONResponse(status_code=status, content=body)
