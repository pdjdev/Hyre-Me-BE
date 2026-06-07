from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session

from app.database import get_db, engine
from app import models, schemas, auth

# models.Base.metadata.create_all(bind=engine) # 필요시 사용해 테이블을 자동 생성

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

@app.post("/register", response_model=schemas.UserResponse, summary="회원 가입")
def register_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    이름, 이메일, 비밀번호를 이용해 회원가입합니다.
    """
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="이미 등록된 이메일입니다.")
    
    hashed_password = auth.get_password_hash(user.password)
    new_user = models.User(
        name=user.name,
        email=user.email,
        password=hashed_password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/login", response_model=schemas.TokenResponse, summary="로그인")
def login_user(user: schemas.UserLogin, db: Session = Depends(get_db)):
    """
    이메일, 비밀번호를 이용해 로그인합니다.
    
    응답에서 받은 access_token을 Authorization 헤더에 "Bearer {token}" 형식으로 포함시켜 인증된 요청을 보냅니다.
    """
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not auth.verify_password(user.password, db_user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다."
        )
    
    access_token = auth.create_access_token(data={"sub": db_user.id})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": db_user.id,
        "name": db_user.name
    }

@app.get("/me", response_model=schemas.UserResponse, summary="현재 사용자 정보 조회")
def get_current_user_info(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    현재 로그인한 사용자의 정보를 조회합니다.
    
    Authorization 헤더에 "Bearer {token}" 형식으로 토큰을 포함시켜야 합니다.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 필요합니다."
        )
    
    try:
        token = authorization.split(" ")[1]
    except IndexError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰 형식이 올바르지 않습니다. 'Bearer {token}' 형식을 사용하세요."
        )
    
    current_user = auth.get_current_user(token, db)
    return current_user