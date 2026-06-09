import os
from sqlalchemy import Column, BigInteger, String, TIMESTAMP, Text, Date, Integer, ForeignKey
from sqlalchemy.sql import func
from app.database import Base

class User(Base):
    __tablename__ = os.getenv("DB_TABLE_USERS", "users")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class UserRefreshToken(Base):
    __tablename__ = os.getenv("DB_TABLE_USER_REFRESH_TOKENS", "user_refresh_tokens")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(
        BigInteger,
        ForeignKey(f"{os.getenv('DB_TABLE_USERS', 'users')}.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    token = Column(String(512), nullable=False)
    expires_at = Column(TIMESTAMP, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class PortfolioProfile(Base):
    __tablename__ = os.getenv("DB_TABLE_PORTFOLIO_PROFILES", "portfolio_profiles")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, unique=True, index=True)
    education = Column(String(255), nullable=True)
    gpa = Column(String(50), nullable=True)
    core_skills_text = Column(Text, nullable=True)
    self_intro_keywords = Column(Text, nullable=True)
    resume_file_url = Column(String(255), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class PortfolioExperience(Base):
    __tablename__ = os.getenv("DB_TABLE_PORTFOLIO_EXPERIENCES", "portfolio_experiences")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    category = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    organization = Column(String(255), nullable=True)
    period_text = Column(String(100), nullable=True)
    role = Column(String(100), nullable=True)
    tech_stack = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    achievement = Column(Text, nullable=True)
    learned = Column(Text, nullable=True)
    related_skills = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=True, default=0)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class PortfolioCertification(Base):
    __tablename__ = os.getenv("DB_TABLE_PORTFOLIO_CERTIFICATIONS", "portfolio_certifications")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    issuer = Column(String(255), nullable=True)
    acquired_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class PortfolioLanguage(Base):
    __tablename__ = os.getenv("DB_TABLE_PORTFOLIO_LANGUAGES", "portfolio_languages")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    test_name = Column(String(100), nullable=False)
    score = Column(String(50), nullable=True)
    grade = Column(String(50), nullable=True)
    acquired_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class Company(Base):
    __tablename__ = os.getenv("DB_TABLE_COMPANIES", "companies")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    role = Column(String(255), nullable=False)
    deadline_text = Column(String(100), nullable=True)
    status = Column(String(50), nullable=True, default="PREPARING")
    job_posting_url = Column(String(500), nullable=True)
    requirements = Column(Text, nullable=True)
    preferences = Column(Text, nullable=True)
    core_values = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class GeneratedResume(Base):
    __tablename__ = os.getenv("DB_TABLE_GENERATED_RESUMES", "generated_resumes")

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    company_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    additional_prompt = Column(Text, nullable=True)
    content_markdown = Column(Text, nullable=True)
    status = Column(String(50), nullable=True, default="PENDING")
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
