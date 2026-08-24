# -*- coding: utf-8 -*-
"""3D 생성 엔진 OpenAPI v3 클라이언트.

v2(api.PROVIDER_HOST/v2/openapi)는 2026-11-01 종료 예정이므로 신규 개발은 v3만 쓴다.
v3는 호스트 자체가 다르다: openapi.PROVIDER_HOST/v3

실측으로 확인한 엔드포인트 (2026-08-24):
    GET  /v3/account/balance          -> {"code":0,"data":{"balance":..,"frozen":..}}
    POST /v3/files                    -> 업로드, file_token 반환
    POST /v3/generation/image-to-model
    POST /v3/mesh/segment
    GET  /v3/tasks/{task_id}

생성 결과 model_url 은 5분만 유효하므로 success 직후 즉시 내려받는다.
"""
import time
from pathlib import Path

import requests

from config import PROVIDER_BASE, PROVIDER_MODEL, provider_api_key


class MeshProviderError(RuntimeError):
    pass


TERMINAL_FAIL = ("failed", "cancelled", "banned", "expired", "error")


class MeshProvider:
    def _req(self, method, url, **kw):
        """일시 오류 재시도. 연결 리셋 한 번에 몇 분짜리 작업이 죽으면 안 된다.

        재시도 대상: 연결 오류, 타임아웃, 5xx. 4xx 는 재시도하지 않는다
        (같은 요청은 같은 이유로 또 거절된다).
        """
        import time as _t
        last = None
        for wait in (0, 2, 5, 10):
            if wait:
                _t.sleep(wait)
            try:
                r = self.s.request(method, url, **kw)
                if r.status_code >= 500:
                    last = MeshProviderError(f"http {r.status_code}")
                    continue
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
        raise MeshProviderError(f"재시도 소진: {last}")

    def __init__(self, api_key=None, timeout=60):
        self.key = api_key or provider_api_key()
        if not self.key:
            raise MeshProviderError("MESH_API_KEY 가 없습니다 (환경변수 또는 scripts/run_backend.cmd)")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {self.key}"

    # ── 내부 ──────────────────────────────────────────────────────────
    def _unwrap(self, r, what):
        try:
            d = r.json()
        except ValueError:
            raise MeshProviderError(f"{what}: 비-JSON 응답 (http {r.status_code}) {r.text[:200]}")
        if r.status_code >= 400 or d.get("code") not in (0, None):
            raise MeshProviderError(f"{what} 실패 (http {r.status_code}): {d.get('message') or d}")
        return d.get("data", d)

    # ── 계정 ──────────────────────────────────────────────────────────
    def balance(self):
        r = self._req('GET', f"{PROVIDER_BASE}/account/balance", timeout=self.timeout)
        return self._unwrap(r, "balance")

    # ── 업로드 ─────────────────────────────────────────────────────────
    def upload(self, path):
        """이미지/모델 파일을 올리고 file_token 을 받는다."""
        p = Path(path)
        mime = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".glb": "model/gltf-binary",
        }.get(p.suffix.lower(), "application/octet-stream")
        with p.open("rb") as f:
            r = self._req('POST', f"{PROVIDER_BASE}/files",
                            files={"file": (p.name, f, mime)}, timeout=180)
        d = self._unwrap(r, "upload")
        tok = d.get("file_token") or d.get("token") or d.get("image_token")
        if not tok:
            raise MeshProviderError(f"file_token 을 찾을 수 없습니다: {d}")
        return tok

    # ── 생성 ──────────────────────────────────────────────────────────
    def image_to_model(self, file_token, model=None, texture=True,
                       face_limit=None, generate_parts=False, extra=None):
        """이미지 -> 3D. generate_parts=True 면 파트 분리 모델도 함께 만든다(추가 과금).

        v3는 file 을 객체로 받는다. 평문 file_token 문자열을 보내면 400.
        """
        body = {
            "file": {"file_token": file_token},
            "model": model or PROVIDER_MODEL,
            "texture": bool(texture),
        }
        if face_limit:
            body["face_limit"] = int(face_limit)
        if generate_parts:
            body["generate_parts"] = True
        if extra:
            body.update(extra)
        r = self._req('POST', f"{PROVIDER_BASE}/generation/image-to-model",
                        json=body, timeout=self.timeout)
        d = self._unwrap(r, "image-to-model")
        tid = d.get("task_id") or d.get("id")
        if not tid:
            raise MeshProviderError(f"task_id 없음: {d}")
        return tid

    def multiview_to_model(self, file_tokens, model=None, texture=True, extra=None):
        """여러 실제 뷰로 3D 를 만든다.

        측면 한 장만으로는 반대쪽 quarter, 아웃솔 트레드, 평면 폭, 힐 뒷면이
        보이지 않는다. 치수를 맞춰도 형상 정확도는 별개다.
        규약 순서는 front, left, back, right 이고 없는 뷰는 빈 객체로 채운다.
        """
        order = ("front", "left", "back", "right")
        files = [{"file_token": file_tokens[k]} if file_tokens.get(k) else {}
                 for k in order]
        if not any(files):
            raise MeshProviderError("뷰가 하나도 없습니다")
        body = {"files": files, "model": model or PROVIDER_MODEL, "texture": bool(texture)}
        if extra:
            body.update(extra)
        r = self._req('POST', f"{PROVIDER_BASE}/generation/multiview-to-model",
                        json=body, timeout=self.timeout)
        d = self._unwrap(r, "multiview-to-model")
        tid = d.get("task_id") or d.get("id")
        if not tid:
            raise MeshProviderError(f"task_id 없음: {d}")
        return tid

    def mesh_complete(self, segmentation_task_id, mode=None, extra=None):
        """세그멘테이션 파트를 닫힌 솔리드로 완성한다.

        입력은 반드시 세그멘테이션 task id 다. 복셀 리메시보다 근거 있는 복구
        경로지만 실제 제조 내부 구조를 보장하지는 않으므로 C1 proxy 로 다룬다.
        """
        body = {"input": segmentation_task_id}
        if mode:
            body["mode"] = mode
        if extra:
            body.update(extra)
        r = self._req('POST', f"{PROVIDER_BASE}/mesh/complete", json=body, timeout=self.timeout)
        d = self._unwrap(r, "mesh/complete")
        tid = d.get("task_id") or d.get("id")
        if not tid:
            raise MeshProviderError(f"task_id 없음: {d}")
        return tid

    def mesh_segment(self, source, model=None, granularity=None,
                     split_by_connectivity=True):
        """모델을 의미 파트로 분리. source 는 task_id / file_token / URL."""
        body = {"input": source}
        if model:
            body["model"] = model
            # granularity·connectivity 는 v2 계열 세그멘테이션 모델에서만 유효하다.
            if granularity:
                body["segmentation_granularity"] = granularity
            body["split_by_connectivity"] = bool(split_by_connectivity)
        r = self._req('POST', f"{PROVIDER_BASE}/mesh/segment", json=body, timeout=self.timeout)
        d = self._unwrap(r, "mesh/segment")
        tid = d.get("task_id") or d.get("id")
        if not tid:
            raise MeshProviderError(f"segmentation task_id 없음: {d}")
        return tid

    # ── 폴링 ──────────────────────────────────────────────────────────
    def task(self, task_id):
        r = self._req('GET', f"{PROVIDER_BASE}/tasks/{task_id}", timeout=self.timeout)
        return self._unwrap(r, f"task {task_id}")

    def wait(self, task_id, timeout_sec=900, poll=3.0, on_progress=None):
        t0 = time.time()
        last = None
        while True:
            d = self.task(task_id)
            st = (d.get("status") or "").lower()
            pr = d.get("progress", 0)
            if on_progress and (st, pr) != last:
                on_progress(st, pr)
                last = (st, pr)
            if st in ("success", "succeeded", "completed"):
                return d
            if st in TERMINAL_FAIL:
                raise MeshProviderError(f"task {task_id} {st}: {d.get('message') or d}")
            if time.time() - t0 > timeout_sec:
                raise MeshProviderError(f"task {task_id} 폴링 타임아웃 ({timeout_sec}s, 마지막 상태 {st})")
            time.sleep(poll)

    # ── 다운로드 ───────────────────────────────────────────────────────
    @staticmethod
    def _model_url(task_data):
        out = task_data.get("output") or {}
        for k in ("model_url", "pbr_model", "model", "base_model", "segmented_model"):
            v = out.get(k)
            if isinstance(v, dict):
                v = v.get("url")
            if v:
                return v
        return None

    def download_model(self, task_data, out_path):
        url = self._model_url(task_data)
        if not url:
            out = task_data.get("output") or {}
            raise MeshProviderError(f"모델 URL 없음. output 키: {list(out.keys())}")
        # 서명 URL이므로 Authorization 헤더 없이 받는다.
        # 서명 URL 다운로드도 재시도. 5분 유효라 실패하면 기회가 없다.
        import time as _t
        last = None
        for wait in (0, 2, 5):
            if wait:
                _t.sleep(wait)
            try:
                r = requests.get(url, timeout=600)
                r.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout,
                    requests.HTTPError) as e:
                last = e
        else:
            raise MeshProviderError(f"다운로드 재시도 소진: {last}")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return out_path

    # ── 편의 래퍼 ──────────────────────────────────────────────────────
    def image_to_glb(self, image_path, out_glb, on_progress=None, **kw):
        tok = self.upload(image_path)
        tid = self.image_to_model(tok, **kw)
        data = self.wait(tid, on_progress=on_progress)
        path = self.download_model(data, out_glb)
        return {"task_id": tid, "glb": str(path), "raw": data}

    def segment_to_glb(self, source_task_id, out_glb, on_progress=None, **kw):
        tid = self.mesh_segment(source_task_id, **kw)
        data = self.wait(tid, on_progress=on_progress)
        path = self.download_model(data, out_glb)
        return {"task_id": tid, "glb": str(path), "raw": data}


if __name__ == "__main__":
    import sys, json
    c = MeshProvider()
    print("balance:", json.dumps(c.balance(), ensure_ascii=False))
    if len(sys.argv) > 1:
        res = c.image_to_glb(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "out.glb",
                             on_progress=lambda s, p: print(f"  {s} {p}%", flush=True))
        print("saved:", res["glb"], "task:", res["task_id"])
