# -*- coding: utf-8 -*-
"""Starter — VRINGON-BE 의 vringon-starter 에 해당하는 진입 모듈.

    .venv\\Scripts\\python.exe server\\app.py     ->  http://127.0.0.1:5270

여기서는 조립만 한다: 도메인 라우터(modules/*_api.py)를 모으고, CORS 와
캐시 정책 미들웨어를 붙이고, 정적 화면을 마운트한다. 업무 로직은 전부
도메인 라우터와 엔진(평면 모듈)에 있다. 엔드포인트 경로는 계획서 §16 그대로다.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import WEB


def create_app():
    app = FastAPI(title="VRINGON Cost — 신발 Design-to-Should-Cost")

    # 공개 정적 페이지(GitHub Pages)가 이 백엔드를 부를 수 있게 허용한다.
    # 추가 origin 은 환경변수 CORS_ORIGINS (쉼표 구분) 로 넣는다.
    origins = ["https://jhkim1543.github.io", "http://localhost:5270",
               "http://127.0.0.1:5270"]
    origins += [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",")
                if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["*"], allow_headers=["*"])

    # 도메인 라우터 조립. 새 도메인은 modules/<이름>_api.py 를 만들고
    # 여기 한 줄을 더한다.
    from modules import (bom_api, costing_api, devtools_api, generation_api,
                         geometry_api, material_api, pricing_api, project_api,
                         segmentation_api)
    for mod in (project_api, geometry_api, segmentation_api, material_api,
                bom_api, costing_api, pricing_api, generation_api,
                devtools_api):
        app.include_router(mod.router)

    # 개발 중에는 정적 자산을 캐시하지 않는다. 캐시된 옛 스크립트를 보고
    # "고쳤는데 그대로다" 로 오판하는 일을 막는다.
    @app.middleware("http")
    async def _no_cache(request, call_next):
        resp = await call_next(request)
        if not request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp

    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5270))
    uvicorn.run(app, host="127.0.0.1", port=port)
