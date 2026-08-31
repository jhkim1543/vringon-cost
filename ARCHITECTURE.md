# VRINGON 정합 구조 — 분석과 매핑

RebuilderAI 의 VRINGON 저장소 4곳(Vringon-BE, VRINGON-WEB, design-system,
vringon-ai-workers-services)을 읽기 전용으로 분석하고, 이 Cost 플랫폼을 같은
구조·디자인 시스템으로 재구성했다. 향후 VRINGON 에 붙는다는 가정의 설계다.
(RebuilderAI 조직 저장소에는 아무것도 쓰지 않았다. 작업은 전부 jhkim 쪽이다.)

## 1. VRINGON 백엔드가 실제로 조직된 방식 (Vringon-BE)

Java 25 + Spring Boot 모놀리식이되, **도메인별 Gradle 모듈 경계**로 MSA 전환을
대비한다.

```
vringon-core/        공통: 도메인 엔티티·레포지토리, config, converter, 예외 체계
vringon-user/        도메인 API 모듈: api/<도메인>/Controller · Service · model/request|response
vringon-edit/        도메인 API 모듈 (AI 리소스 생성 요청·조회)
vringon-vms/         도메인 API 모듈
vringon-extension/   도메인 API 모듈
vringon-messaging/   SQS Consumer·Worker (발행은 각 도메인에서)
vringon-starter/     모놀리식 진입 모듈. 환경설정 통합, 테스트가 여기 모임
```

핵심 규약:
- **오류 코드 체계**: 도메인별 enum `VO-<도메인>-NNN` (HttpStatus + code +
  message), `VringonException` + `GlobalExceptionHandler` 가 ErrorResponse 로
  변환. 영구 실패와 일시 오류를 코드로 구분한다 (VO-TASK-002 주석 참조).
- 도메인 모듈은 core 의 추상화만 쓰고 서로를 직접 참조하지 않는다.
- 인프라: EB + RDS(MySQL) + Redis + S3 + SQS + Secrets Manager, Flyway 마이그레이션.

AI 쪽은 별도 저장소(vringon-ai-workers-services, Python)로 분리되어
`src/common`(공유 런타임·capability)과 `src/services`(백엔드 대면 태스크)로
나뉘고, **tests/architecture** 에 임포트 경계 테스트를 둔다. 백엔드와는
SQS(AsyncAPI 계약) + DynamoDB(작업 상태) + S3(산출물)로 만난다.

## 2. 프런트엔드와 디자인 시스템

- VRINGON-WEB: `core/`(공유 api·components·layout·pages·stores·styles) +
  `products/{vringon,asics,mcm}`(브랜드별 진입) 모노레포. 레이아웃은
  **상단 GNB(높이 48px)** + 본문이며, 배경은 경로별 surface 모드
  (`--surface-surface-01-dark` 등 CSS 변수)로 정해진다.
- design-system: Figma 승인 토큰의 단일 출처. W3C 토큰 JSON
  (common/light/dark) → tokens.css. 구성:
  - 팔레트: grayscale gray00~gray12, blue01~10(**primary = blue06 #444ae8**),
    red/green/orange 12단계
  - 시맨틱(dark): surface.01~05(gray12→gray07), text.primary(gray01)/secondary/
    tertiary, border.default(gray08)/dim, button.primary(blue05, hover blue06,
    pressed blue08), text.accent(blue05), success(green02)/warning(orange03)/
    fail(red04)
  - 타이포: **Pretendard**, 역할 스케일 Hero/Header/Title/Body/Label/Caption/
    Button (fontSize 10~56, 규정된 lineHeight·weight)
  - radius 4/6/8/12/16/24/full — 컴포넌트별 지정(button-md=4, input-lg=6,
    card=8, modal=16, tag=full), spacing 2~96 스케일
- 소비 규칙(디자인 시스템 스킬): 토큰 변수만 사용, 키보드·포커스·모션 감소
  동작 보존.

## 3. 이 저장소의 매핑

Cost 엔진은 파이썬이므로, BE 의 Java 구조를 흉내내는 대신 **역할을 매핑**한다.
(파이썬 서비스 선례인 ai-workers 의 common/services 분리와 같은 원리다.)

| VRINGON | vringon-cost | 내용 |
|---|---|---|
| vringon-core | `server/` 평면 엔진 모듈 | geometry·bom·consumption·pricing·costing·units·catalog·canonical — 순수 계산, HTTP 를 모른다 |
| 도메인 API 모듈 | `server/modules/<도메인>/router.py` | project·geometry·segmentation·material·bom·costing·pricing·generation 별 APIRouter |
| vringon-starter | `server/app.py` | 라우터 조립 + 미들웨어 + 정적 서빙만 |
| VO-도메인-NNN | `server/core/errors.py` | `VC-<도메인>-NNN` 코드 체계, CostError → {code, message} 응답 |
| tests/architecture | `tests/test_architecture.py` | 엔진은 라우터를 임포트하지 못하고, 라우터끼리는 서로 임포트하지 못한다 |
| design-system tokens | `web/tokens.css` | 필요한 시맨틱 부분집합을 같은 이름 규칙(`--surface-01` 등)으로 선언. 원본 JSON·아이콘·브랜드 자산은 복제하지 않는다(사유 저장소) |
| GNB 레이아웃 | `web/index.html` | 상단 48px GNB, Pretendard, surface 위계 |

향후 VRINGON 에 붙일 때의 경계:
- 이 서비스는 **도메인 모듈 하나(cost)** 또는 **services 워커 하나**로 들어간다.
  라우터가 이미 도메인별로 쪼개져 있어 컨트롤러 이식이 기계적이다.
- 생성 작업(3D)은 지금 스레드+폴링인데, VRINGON 에서는 messaging(SQS) 계약으로
  바꾸면 된다. 작업 상태 dict(JOBS)가 DynamoDB 자리다.
- 인증·팀·테넌트는 붙일 때 vringon-user/core 것을 쓴다. 여기서는 만들지 않는다.

## 4. 오류 코드 등록부 (VC-*)

| 코드 | 뜻 |
|---|---|
| VC-PROJ-001 | 프로젝트 ID 형식 위반 |
| VC-PROJ-002 | 아직 계산되지 않음 |
| VC-GEO-001 | 파트 메시 없음 (세그멘테이션 필요) |
| VC-GEO-002 | 이 배포본에는 원본 메시가 없음 (재계산 불가, 열람만) |
| VC-GEO-003 | 캘리브레이션 없음 |
| VC-MAT-001 | 알 수 없는 소재 |
| VC-MAT-002 | 해당 분기 단가 없는 소재 |
| VC-BOM-001 | BOM 없음 |
| VC-BOM-002 | 승인자·근거 누락 |
| VC-GATE-001 | 알 수 없는 게이트 |
| VC-GATE-002 | 게이트 승인 증거 누락 |
| VC-GEN-001 | 지원하지 않는 이미지 형식 |
| VC-GEN-002 | 이미지 크기 초과 |
| VC-GEN-003 | 생성 엔진 키 없음 |

일시 오류(재시도 가능)와 영구 실패의 구분은 BE 의 VO-TASK-002 주석 원칙을
따른다. 예: VC-GEO-002 는 영구(파일이 없는 배포본), 생성 폴링 실패는 일시.
