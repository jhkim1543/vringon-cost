# 도메인 API 모듈. VRINGON-BE 의 도메인 모듈(api/<도메인>/Controller)에 해당.
# 라우터는 얇다: 검증하고, 엔진(pipeline·costing 등)을 부르고, 오류 코드를 붙인다.
# 라우터끼리는 서로 임포트하지 않는다 (tests/test_architecture.py 가 지킨다).
