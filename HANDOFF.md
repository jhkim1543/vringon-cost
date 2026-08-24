# VRINGON Cost — 신발 Design-to-Should-Cost 데모

`신발_BOM_Cost_최종_개발계획_2026-08-23.md` 를 실제로 도는 코드로 구현한 것.
디자인 이미지 → 3D 생성 엔진 3D → metric 보정 → 세그먼트 → Hidden BOM →
소요량 → 단가 → 결정론적 P10/P50/P90 원가까지 한 줄로 이어진다.

**공개 데모: https://jhkim1543.github.io/vringon-cost/**

```bash
run.cmd
```
→ http://127.0.0.1:5270  (기본 프로젝트 `DEMO-RUN-001`, `?p=<id>` 로 전환)

정적 배포본은 `tools/build_static.py` 가 `docs/` 를 굽고 `web/static-api.js` 가
`fetch('/api/...')` 를 그 JSON 으로 가로챈다. 읽기 전용이며 쓰기 동작은 막는다
(조용히 실패하면 화면이 거짓말을 한다). Pages 는 main 브랜치 `/docs`.

---

## 1. 이 데모의 핵심 주장

**AI가 원가를 뱉지 않는다.** AI는 파트를 제안할 뿐이고, 원가는
`수량 × 단가 + 공정시간 × 공장Rate + Tooling 상각` 을 결정론적으로 더해서 나온다.
그리고 **입력이 없으면 0 으로 숨기지 않고 Blocked 로 세운다.**

화면의 모든 원가 라인은 근거 패널에서 다음까지 역추적된다:

```
규칙 R-014 (조건 cemented_sole==true)
 → 도포면적 0.038963 m²  [proxy: 라스트 바닥 + 둘레×12mm]
 → 습도포량 0.12 kg/m²   [factory_required]
 → 도포 2회             [workbook]
 → 전이효율 0.9          [factory_required]
 → 0.010390 kg → 켤레환산 ×2 → 0.020780 kg
 → $2.815/kg (quarterly_snapshot, Concept only, 신뢰도 C)
 → $0.0585
 → C2 차단: transfer_efficiency·wet_coat_kg_m2 공장 확인 필요
```

---

## 2. 실측 확인한 3D 생성 엔진 스펙 (2026-08-24)

v2(`api.PROVIDER_HOST/v2/openapi`)는 **2026-11-01 종료**. v3는 호스트가 다르다.

| 용도 | 엔드포인트 |
|---|---|
| base | `https://openapi.PROVIDER_HOST/v3` |
| 잔액 | `GET /account/balance` |
| 업로드 | `POST /files` (multipart 필드명 `file`) → `file_token` |
| 이미지→3D | `POST /generation/image-to-model` |
| 세그멘테이션 | `POST /mesh/segment` (`input`=task_id) |
| 폴링 | `GET /tasks/{task_id}` |

**함정 셋**
1. `image-to-model` 은 `file` 을 **객체**로 받는다. `{"file_token": "..."}` 를 평문으로
   보내면 `400 file is required`. 올바른 형태는 `{"file": {"file_token": "..."}}`.
2. `model` 허용값은 서버가 알려준다 — 일부러 틀린 값을 보내면 목록이 온다.
   현재: `P1-20260311, P2-20260801, v2.5-20250123, v3.0-20250812, v3.1-20260211`.
   기본값은 범용 최신인 `v3.1-20260211` (P계열은 캐릭터 특화).
3. `output.model_url` 은 **5분만 유효**. 성공 즉시 내려받는다.

과금 실측: 생성 30 크레딧, 세그멘테이션 40 크레딧.

키는 리포에 없다. `MESH_API_KEY` 환경변수 → 없으면 `../scripts/run_backend.cmd` 에서 읽는다.

---

## 3. 실제로 물렸던 함정 (전부 회귀 테스트 있음)

| 증상 | 원인 | 테스트 |
|---|---|---|
| 파트 위치 특징이 전부 0.5 로 수렴 | `scene.graph.get(geometry_name=)` 가 trimesh 5 에서 TypeError → `except: pass` 가 삼켜서 **노드 변환 미적용**, 모든 파트가 로컬 원점에 겹침 | `test_scene_parts_apply_node_transforms` |
| 솔이 아래로 안 옴 | glTF Y-up 가정. 생성 결과는 월드축에 정렬돼 있지 않다 → **볼록껍질 최대 대향 면적(접지면)** 에서 up 을 뽑는다 | `test_canonical_frame_puts_sole_at_bottom` |
| toe/heel 반대 | 뒤꿈치가 더 높다는 규칙의 부호를 거꾸로 씀 | 위 테스트에 포함 |
| 미드솔 부피 1.3억 cm³ | `VoxelGrid.marching_cubes` 는 **복셀 인덱스 좌표계** 메시를 준다. `vg.transform` 미적용 시 pitch⁻³ 배 | `test_repair_closes_open_mesh_with_sane_volume` |
| 폴리백이 켤레당 1 kg | `count` 로 세고 kg 단가를 곱함 → 소재비의 40%가 포장재 | `test_packaging_is_not_one_kilogram` |
| 레이스 이중계상 | 세그먼트 Lace + 규칙 R-016 Lace 둘 다 삽입 | `test_no_duplicate_lace_line` |
| 소재비가 딱 절반 | **3D 는 한 짝, 원가는 켤레.** 기하 유래 수량은 ×2 | `test_roll_consumption_and_pair_factor` |

---

## 4. 구조

```
server/
  mesh_provider.py    v3 클라이언트 (실측 검증)
  geometry.py    shoe_frame(장축·up·toe/heel) · calibration · mesh_qa · part_metrics
  repair.py      열린 껍질 → 닫힌 솔리드 (fill_holes → 복셀 리메시)
  canonical.py   기하 특징 → canonical part 제안 (헝가리안 + 신뢰도)
  measures.py    레시피가 요구하는 제조 치수 (measured / proxy / blocked)
  bom.py         측정 파트 + 규칙 파트 → mBOM. 조건식은 화이트리스트 (eval 없음)
  consumption.py roll/sheet/molded/chemical/thread/count + 켤레 환산
  pricing.py     우선순위 선택 · eligibility · stale 이관 · price_proxy
  costing.py     롤업 · Blocked-not-zero · C0~C4 등급
  pipeline.py    프로젝트 상태(state.json)와 단계 실행
  app.py         FastAPI
web/             three.js 뷰어 + 7단계 워크플로 + 근거 패널
tools/
  seed_from_xlsx.py  워크북 18시트 → data/seed/*.json
  preview_parts.py   파트 실루엣 (2D)
  preview_3d.py      파트 셰이딩 3D (정규 프레임)
data/
  seed/              워크북에서 뽑은 원본
  material_specs.json  워크북에 없는 공학 파라미터 (폭·밀도·시트면적)
  projects/<id>/     raw_model.glb · segmented.glb · viewer.glb · state.json · cost.json
```

### 워크북에서 온 것 / 여기서 채운 것

워크북(65 파트소재맵 · 62 단가관측 · 131 원자재지수 · 34 분기단가 · 21 레시피)에는
**가격은 있는데 계산에 필요한 공학 파라미터가 없다.** `data/material_specs.json` 이
그걸 채우고, 각 값에 출처를 단다:

- `workbook` 원본 그대로
- `public_reference` 업계 공개 통상값 (EVA 성형 밀도 220 kg/m³ 등)
- `assumption` 데모 기본값 (유효 폭 1.5 m, 네스팅 수율 0.82 등)
- `factory_required` 공장 확인 없이는 **C2 차단** (접착 도포량·전이효율)

UI 근거 패널이 이 출처를 그대로 보여준다. 숫자를 믿을지 말지 사용자가 판단한다.

---

## 5. 현재 데모 결과 (DEMO-RUN-001, 실제 생성 결과물)

입력: `신발 디자인/매쉬1.JPG` (메시 어퍼 + EVA 미드솔 + 러버 아웃솔), 외부 길이 300 mm.

```
세그먼트 10개 → canonical 매핑 8/8 일치 (신뢰도 0.47~0.88, 5개 needs_review)
BOM 24 라인 (측정 8 + 규칙 16)
Material            P10 $1.405  P50 $2.701  P90 $3.997   Partial
Direct Labor        Blocked — 공장 SAM·rate 미입력
Machine             Blocked
Tooling             Blocked — 금형 견적 미입력
Provisional Total   P50 $3.298
등급 C1 · FOB Blocked as FOB
```

10개 파트 전부 non-watertight → 미드솔·아웃솔 부피가 **원래 전부 차단**된다.
`부피 막힌 파트 복구` 를 누르면 복셀 리메시로 닫아 116.6 / 20.0 / 12.6 / 10.3 cm³ 를 얻고,
라인에 "복구본 사용" 이 붙는다. **측정으로 위장하지 않는다.**

---

## 6. 실제 서비스로 갈 때 첫 3가지

1. **패턴/네스팅** — 지금 면적은 3D 표면적 × 상수 패턴계수다. 승인 DXF + PackingSolver 를
   붙여야 C2 소요량이 된다. (`measures.py` 의 `method: proxy` 가 전부 여기 해당)
2. **공장 데이터** — `15_공정인건비`·`17_Tooling마스터` 가 전부 TBD 라 노무·기계·금형이 막혀 있다.
   공장 operation bulletin 하나만 들어와도 FOB 가 열린다.
3. **세그멘테이션 품질** — 기하 세그멘테이션은 라벨이 없고 제조 파트와 1:1 이 아니다.
   사내 세그멘테이션 모델을 `SegmentInput` 계약(`segment_id/label/mesh_path/confidence`)에
   맞춰 `canonical.propose` 를 대체하는 것이 가장 효과가 크다.

---

## 7. 검증

```bash
.venv\Scripts\python.exe -m pytest tests -q     # 27 passed
```
