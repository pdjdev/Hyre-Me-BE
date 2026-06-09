import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from app.database import get_db
from app import models, schemas

load_dotenv()

router = APIRouter(prefix="/api/auth", tags=["계정"])

SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key_here_change_this_in_production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
PASSWORD_SALT = os.getenv("PASSWORD_SALT", "default_salt_change_in_production")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """비밀번호 검증"""
    return get_password_hash(plain_password) == hashed_password

def get_password_hash(password: str) -> str:
    """비밀번호 해싱 (SHA256 + 솔트)"""
    return hashlib.sha256(f"{password}{PASSWORD_SALT}".encode()).hexdigest()

def _create_token(data: dict, token_type: str, expires_delta: Optional[timedelta] = None) -> tuple[str, datetime]:
    """JWT 토큰 생성"""
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    elif token_type == "refresh":
        expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "token_type": token_type})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt, expire


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    token, _ = _create_token(data=data, token_type="access", expires_delta=expires_delta)
    return token


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    token, _ = _create_token(data=data, token_type="refresh", expires_delta=expires_delta)
    return token


def create_refresh_token_with_expiry(data: dict, expires_delta: Optional[timedelta] = None) -> tuple[str, datetime]:
    return _create_token(data=data, token_type="refresh", expires_delta=expires_delta)

def verify_token(token: str, expected_token_type: str = "access") -> dict:
    """JWT 토큰 검증 및 데이터 추출"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("token_type")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="유효하지 않은 토큰입니다."
            )
        if token_type != expected_token_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="토큰 유형이 올바르지 않습니다."
            )
        return {"user_id": int(user_id), "token_type": token_type}
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 유효하지 않습니다."
        )

def get_token_from_request(request: Request) -> str:
    """Authorization 헤더에서 토큰 추출"""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization 헤더가 필요합니다."
        )
    
    try:
        scheme, token = auth_header.split()
        if scheme.lower() != "bearer":
            raise ValueError("Invalid auth scheme")
        return token
    except (ValueError, IndexError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 Authorization 헤더 형식입니다. (Bearer token 형식이어야 합니다)"
        )

def get_current_user(token: str = Depends(get_token_from_request), db: Session = Depends(get_db)) -> models.User:
    """현재 로그인한 사용자 조회"""
    token_data = verify_token(token, expected_token_type="access")
    user = db.query(models.User).filter(models.User.id == token_data["user_id"]).first()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다."
        )
    
    return user


@router.post("/register", response_model=schemas.UserResponse, summary="회원 가입")
def register_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    이름, 이메일, 비밀번호를 이용해 회원가입합니다.
    """
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="이미 등록된 이메일입니다.")

    hashed_password = get_password_hash(user.password)
    new_user = models.User(
        name=user.name,
        email=user.email,
        password=hashed_password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=schemas.TokenResponse, summary="로그인")
def login_user(user: schemas.UserLogin, db: Session = Depends(get_db)):
    """
    이메일, 비밀번호를 이용해 로그인합니다.

    응답에서 받은 access_token을 Authorization 헤더에 "Bearer {token}" 형식으로 포함시켜 인증된 요청을 보냅니다.
    """
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not verify_password(user.password, db_user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다."
        )

    access_token = create_access_token(data={"sub": db_user.id})
    refresh_token, refresh_expires_at = create_refresh_token_with_expiry(data={"sub": db_user.id})

    existing_refresh_token = (
        db.query(models.UserRefreshToken)
        .filter(models.UserRefreshToken.user_id == db_user.id)
        .first()
    )

    if existing_refresh_token:
        existing_refresh_token.token = refresh_token
        existing_refresh_token.expires_at = refresh_expires_at
    else:
        existing_refresh_token = models.UserRefreshToken(
            user_id=db_user.id,
            token=refresh_token,
            expires_at=refresh_expires_at,
        )
        db.add(existing_refresh_token)

    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": db_user.id,
        "name": db_user.name
    }


@router.post("/refresh", response_model=schemas.TokenResponse, summary="토큰 재발급")
def refresh_token(payload: schemas.RefreshTokenRequest, db: Session = Depends(get_db)):
    """
    Refresh Token을 검증하고 새 Access Token 및 Refresh Token을 발급합니다.
    """
    token_data = verify_token(payload.refresh_token, expected_token_type="refresh")

    stored_refresh_token = (
        db.query(models.UserRefreshToken)
        .filter(models.UserRefreshToken.user_id == token_data["user_id"])
        .first()
    )

    if not stored_refresh_token or stored_refresh_token.token != payload.refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="저장된 리프레시 토큰과 일치하지 않습니다."
        )

    db_user = db.query(models.User).filter(models.User.id == token_data["user_id"]).first()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다."
        )

    access_token = create_access_token(data={"sub": db_user.id})
    new_refresh_token, new_refresh_expires_at = create_refresh_token_with_expiry(data={"sub": db_user.id})

    stored_refresh_token.token = new_refresh_token
    stored_refresh_token.expires_at = new_refresh_expires_at
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "user_id": db_user.id,
        "name": db_user.name
    }


@router.get("/me", response_model=schemas.UserResponse, summary="현재 사용자 정보 조회")
def get_current_user_info(
    current_user: models.User = Depends(get_current_user)
):
    """
    현재 로그인한 사용자의 정보를 조회합니다.

    Authorization 헤더에 "Bearer {token}" 형식으로 토큰을 포함시켜야 합니다.
    """
    return current_user


@router.patch("/me", response_model=schemas.UserResponse, summary="현재 사용자 정보 수정")
def update_current_user_info(
    payload: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    현재 로그인한 사용자의 이름 또는 비밀번호를 수정합니다.

    이름과 비밀번호 중 하나 이상을 보내야 합니다.
    """
    if payload.name is None and payload.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="수정할 정보를 하나 이상 제공해야 합니다."
        )

    if payload.name is not None:
        current_user.name = payload.name

    if payload.password is not None:
        current_user.password = get_password_hash(payload.password)

    db.commit()
    db.refresh(current_user)
    return current_user
