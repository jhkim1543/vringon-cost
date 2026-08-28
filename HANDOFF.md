# VRINGON Cost — 신발 Design-to-Should-Cost 데모

`신발_BOM_Cost_최종_개발계획_2026-08-23.md` 를 실제로 도는 코드로 구현한 것.
디자인 이미지 → 3D 생성 엔진 3D → metric 보정 → 세그먼트 → Hidden BOM →
소요량 → 단가 → 결정론적 P10/P50/P90 원가까지 한 줄로 이어진다.

**공개 데모: https://jhkim1543.github.io/vringon-cost/**  ·  [내 디자인으로 돌려보기](HOWTO-업로드.md)

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
| 3D 표면에 검은 선이 그어짐 | 파트를 따로 내보내면 각자 자기 면만으로 정점 법선을 평균한다. 경계에서 법선이 중앙값 23도, 최대 140도 벌어져 조명이 튄다. 기하는 붙어 있으므로 구멍이 아니라 셰이딩 문제 | `test_viewer_parts_share_vertex_normals` |
| 파트별 데시메이션이 만든 실제 틈 | 파트마다 따로 줄이면 경계 정점이 제각각 움직인다. 전체를 허용오차 병합 후 한 번에 줄이고 라벨을 이전한다 | `test_viewer_glb_has_no_part_seam_gaps` |

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

## 5. 현재 데모 결과 (DEMO-RUN-001, 실제 생성물)

입력은 메시 어퍼에 EVA 미드솔과 러버 아웃솔인 러닝화 측면 사진 한 장,
외부 길이 300 mm 다.

```
세그먼트 10개, 배정 커버리지 10/10, 검토 필요 5건
BOM 24 라인 (측정 8, 규칙 16), 전부 C1 상한
확인된 소재비 소계   P10 $2.197  P50 $3.676  P90 $5.154
전체 제조원가        계산 불가
FOB                 계산 불가
차단 버킷            Direct Labor, Machine, Tooling Amortization
등급                 C1
질량 정합성          580 g 대비 목표 600 g, 판정 suspect_over_estimate
```

**총액을 내보내지 않는다.** 노무, 기계, 금형 중 하나라도 막히면 전체
제조원가와 FOB 는 null 이다. 소재비 소계에 간접비와 마진을 곱하면 그 비율의
기준이 전체 제조비인데 분자만 소재비라 숫자가 조용히 왜곡되기 때문이다.

### 복구 부피는 측정이 아니다

10개 파트 전부 열린 껍질이라 솔 부피가 원래 전부 차단된다. 복구 경로는
두 단계로 두고, 결과를 해상도 민감도로 검증한다.

| 계층 | 방법 | 미드솔 CV | 판정 |
|---|---|---|---|
| R4 | 복셀 리메시 | 29.7% | 차단 |
| R3 | 메시 완성 후 복셀 3해상도 평균 | 4.5% | 통과 |

R4 에서 부피가 pitch 1.0, 1.5, 2.0 mm 에 대해 38, 60, 82 cm3 로 거의 선형
증가했다. 복셀이 만든 것은 솔리드가 아니라 두께가 pitch 인 껍질이라는 뜻이고,
그래서 자동 차단된다. R3 로 바꾸면 미드솔 389 cm3, 아웃솔 116 cm3 로
안정화되지만 여전히 `repaired_volume_proxy` 이며 **C1 상한**이다.

질량 검사가 이 값이 아직 과대임을 잡아낸다. 원단 14개 라인이 질량에 들어가지
않았는데도 이미 목표 무게의 97% 라서 `suspect_over_estimate` 로 판정된다.

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
.venv\Scripts\python.exe -m pytest tests -q
```
36 passed. 회귀 테스트가 15건이고 그중 9건이 아래 검토 반영분이다.

## 8. 검토 의견 반영분

| 지적 | 반영 |
|---|---|
| 부분 원가를 총원가처럼 보여줌 | Provisional Total 제거, 전체 원가와 FOB 를 null 로. 부분 상태에서는 간접비와 마진을 아예 계산하지 않는다 |
| 열린 메시를 전부 같은 오류로 취급 | `geometry_role` 도입. 어퍼 패널이 열린 것은 정상, 솔리드가 열린 것은 결함으로 구분 |
| 복구 부피를 단일 숫자로 사용 | 해상도 3종 CV 게이트. 5% 이하 통과, 10% 초과 차단 |
| 복구본이 C2 로 승격될 위험 | `repaired_volume_proxy` 는 등급 상한 C1 로 잠금 |
| 단위 안전성 부족 | `units.py` 차원 검증기. count 와 USD/kg 같은 조합을 즉시 차단 |
| 전역 2배 환산 | `quantity_basis` 로 기준별 환산. 켤레 단위 품목은 곱하지 않는다 |
| 8/8 일치를 정확도로 오해 | 배정 커버리지로 이름을 바꾸고 자동 채택과 검토 필요를 분리 표기 |
| 소재비 과소 원인 미확인 | 질량 정합성 검사 추가. 과소와 과대 양방향 판정 |
| 단일 이미지의 형상 한계 | 멀티뷰 생성 경로 추가 |

아직 반영하지 않은 것은 Gold Benchmark 구축, DXF 기반 네스팅, 시각 회귀
테스트 자동화다. 이 셋은 실제 공장 데이터와 승인 패턴이 있어야 의미가 있다.

## 백엔드 부착 (2026-08-24)

공개 페이지가 정적 결과만 보여주던 것을, 실서버가 있으면 자동으로 실서버 모드로
전환되게 했다. Elastic Beanstalk(기존 3dcad 와 같은 계정, ap-northeast-2)에 올린다.

    aws login                                  콘솔 계정 인증 (브라우저)
    python deploy/eb_bundle.py                 번들 생성 (17.7MB, 공급자명 누출 0 검증됨)
    python deploy/eb_deploy.py --create        처음 한 번: 앱 vringon-cost, 환경 vringon-cost-prod
    python deploy/eb_deploy.py --https --domain cost.rebuilder.ai
    python deploy/eb_deploy.py --verify https://cost.rebuilder.ai

- 생성 키(MESH_API_KEY)와 공급자 주소(MESH_API_BASE)는 로컬 관례 위치에서 읽어
  환경 속성으로만 넘긴다. 리포·번들·화면 어디에도 남지 않는다 (번들 빌드가
  전수 검사한다).
- 번들에는 프로젝트별 viewer.glb, state, cost, 매핑, 입력 이미지만 들어간다.
  원본·세그·복원 GLB(각 40MB 안팎)는 빼므로 씨앗 프로젝트의 단계 재실행 일부는
  서버에서 안 되고, 새 업로드는 전부 된다.
- 정적 페이지 연결: `deploy/backend.json` 에 `{"base": "https://cost.rebuilder.ai"}`
  를 만들고 `tools/build_static.py` 를 다시 돌리면 docs/ 에 주소가 구워진다.
  페이지가 부팅 때 그 서버를 찔러 살아있으면 실서버 모드(업로드·생성·재계산 동작),
  죽어있으면 지금의 정적 모드로 내려앉는다.
- HTTPS 는 필수다. Pages 가 https 라 http 백엔드는 브라우저가 차단한다(mixed content).
  `--https` 는 ACM 발급 인증서를 찾아 443 리스너를 붙이고 Route53 에 CNAME 을 쓴다.
  도메인을 덮는 인증서가 없으면 ACM 에서 먼저 요청·DNS 검증해야 한다.
- 운영에서는 `/api/debug/capture` 가 닫힌다 (EB 가 주는 PORT 로 판정).

## 사내 GPU 서버 부착 (2026-08-24 실배포)

EB 대신(AWS 세션 만료) 사내 GPU 서버의 jhkim 계정 영역에 백엔드를 실제로 올렸다.
다른 서비스와 완전히 분리: 전용 디렉터리, 전용 venv, 전용 고포트, sudo 불사용.

- 위치: `plushgpu:/data/jhkim/vringon-cost-svc/` (app=번들 전개, venv, env(0600), run.sh)
- 기동: `run.sh` 가 재시작 루프로 uvicorn 을 돌리고, crontab `@reboot` 에 등록
  (기존 vringon-cad 항목은 보존). 죽이면: `pkill -u jhkim -f "[u]vicorn server.app:app"`
- 갱신: `python deploy/gpu_deploy.py` 한 줄이면 번들 생성부터 전개·재기동·검증까지
  한다. 서버에서 만들어진 프로젝트와 원본 메시는 보존한다(안 그러면 라이브 생성
  결과가 배포마다 사라진다). 원본 파트 메시까지 맞추려면 `--meshes` (410MB, 느림).
  키·공급자 주소는 서버의 env 파일에만 있다 (리포·번들엔 없음).
- 원본 파트 메시(segmented/completed)가 없으면 예시는 **보기만** 되고 다시 계산이
  안 된다. 실제로 이것 때문에 예시에서 '매핑 확정하고 BOM 으로' 가 막혔었다.
- 주소는 HTTP 고포트라 https 인 GitHub Pages 페이지에 붙이면 mixed content 로
  차단된다. 그래서 공개 페이지 연결(`deploy/backend.json`) 대신 **서버 주소로 직접
  접속**하는 것이 라이브 데모다 (같은 오리진이라 업로드·생성·재계산 전부 동작).
  주소 자체는 공개 리포에 적지 않는다.
- EB 절차(위 절)는 그대로 유효하다. https 도메인이 필요해지면 EB 로 올리면 된다.

## 공개 지수·웹 조사 파이프라인 (2026-08-28)

"딥리서치나 API 로 데이터 공백을 못 메우나"에 대한 답. **메울 수 있는 것과
없는 것이 갈린다.**

되는 것 (구현됨):
- `tools/fetch_benchmarks.py` 세계은행 Pink Sheet(고무 RSS3·TSR20, 면화)와
  ECB 환율을 무료 공개 API 로 수집해 분기 스냅샷과 대조한다. 분기 내 변동이
  ±10% 를 넘으면 "스냅샷 갱신 신호"를 띄운다. 결과는 A1 (Benchmark).
- `tools/research_component_prices.py` 웹 검색 LLM 2종(키는
  ../blueocean-agent/.env)이 부품 층위 단가(미드솔·아웃솔 유닛, engineered
  mesh 등)를 독립 조사하고, URL 없는 숫자는 버리고, 둘 다 찾은 항목만
  상호검증으로 표시해 병합한다. 결과는 A0 (Estimated) 이며
  benchmark_bridge 의 대조 가정을 대체한다.
- 화면 단가 단계의 "시장 지수·조사치 보기" 버튼과 `/api/benchmarks`.

안 되는 것 (원칙):
- **A2 이상(견적·PO·송장)은 API 로 만들 수 없다.** 실거래 문서가 필요하다.
- 지수·조사치는 어떤 경우에도 **계산 단가를 바꾸지 않는다** (테스트로 고정).
  단가는 분기 스냅샷에서만 온다. 리스팅 조사치를 견적처럼 취급하는 순간
  검토가 지적한 "공개 시세를 정답으로 쓰는" 오류로 돌아간다.
- routing·SMV 는 공개 자료로 "참고 범위"는 만들 수 있어도 공장 확정 없이는
  노무비 차단을 풀지 않는다.

월간 점검: `python tools/fetch_benchmarks.py` 를 매월 1회 돌리면 된다.
Pink Sheet 주소는 매월 바뀌므로 랜딩 페이지에서 자동 해석한다.
