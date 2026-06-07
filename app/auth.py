import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key_here_change_this_in_production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
PASSWORD_SALT = os.getenv("PASSWORD_SALT", "default_salt_change_in_production")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """비밀번호 검증"""
    return get_password_hash(plain_password) == hashed_password

def get_password_hash(password: str) -> str:
    """비밀번호 해싱 (SHA256 + 솔트)"""
    return hashlib.sha256(f"{password}{PASSWORD_SALT}".encode()).hexdigest()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """JWT 토큰 생성"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> dict:
    """JWT 토큰 검증 및 데이터 추출"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="유효하지 않은 토큰입니다."
            )
        return {"user_id": user_id}
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 유효하지 않습니다."
        )

def get_current_user(token: str, db: Session = Depends(get_db)) -> models.User:
    """현재 로그인한 사용자 조회"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 필요합니다."
        )
    
    token_data = verify_token(token)
    user = db.query(models.User).filter(models.User.id == token_data["user_id"]).first()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다."
        )
    
    return user
