from datetime import datetime
from fastapi import FastAPI

app = FastAPI(title="hyre-me-BE", version="0.1.0", description="Hyre Me Backend API")

@app.get(
    "/health", 
    summary="헬스 체크"
)
def health_check():
    """
    서버 헬스 체크
    
    서버의 정상 작동 여부를 반환합니다.
    - **status**: 보통 'ok'를 반환하면 정상 작동 중임을 의미합니다.
    """
    return {"status": "ok"}

@app.get(
    "/current-time",
    summary="현재 서버 시간 조회"
)
def current_time():
    """
    서버 시간 반환
    
    현재 접속한 서버의 시간을 확인합니다.
    - **current_time**: 생성된 서버 시간 (ISO 8601 포맷, 예: `2026-05-19T12:34:56.789123`)
    """
    return {"current_time": datetime.now().isoformat()}