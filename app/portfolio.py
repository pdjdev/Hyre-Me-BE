from pathlib import Path
from datetime import date, datetime
from uuid import uuid4
import json

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.inspection import inspect

from app import auth, models, schemas
from app.database import get_db
from app.ai_service import extract_portfolio_data_with_ai, generate_masterpiece_resume

router = APIRouter(prefix="/api")

UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "uploads"
RESUME_UPLOAD_DIR = UPLOAD_ROOT / "resumes"
ALLOWED_RESUME_EXTENSIONS = {".pdf", ".doc", ".docx"}
ALLOWED_RESUME_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

RESUME_FILE_PATH_PREFIX = "/uploads/resumes"


def _dump_model(model):
    # 1. SQLAlchemy ORM 모델인 경우의 처리
    if hasattr(model, "__table__"):
        result = {}
        mapper = inspect(model.__class__)
        for column in mapper.columns:
            value = getattr(model, column.name, None)
            # 날짜 및 시간 데이터를 문자열(ISO 포맷)로 안전하게 직렬화
            if isinstance(value, (datetime, date)):
                value = value.isoformat()
            result[column.name] = value
        return result
    
    # 2. Pydantic v2 및 v1 모델인 경우의 처리
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    
    # 3. 일반 객체인 경우 내장 __dict__를 복사하여 처리
    if hasattr(model, "__dict__"):
        result = model.__dict__.copy()
        result.pop('_sa_instance_state', None)  # SQLAlchemy 내부 상태 값은 제거
        for key, value in result.items():
            if isinstance(value, (datetime, date)):
                result[key] = value.isoformat()
        return result
    
    if isinstance(model, dict):
        return model
    
    return {}


def _dump_model_update(model):
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


def _get_current_user_id(current_user: models.User) -> int:
    return int(current_user.id)


def _get_or_create_profile(db: Session, user_id: int, persist: bool = False) -> models.PortfolioProfile:
    profile = db.query(models.PortfolioProfile).filter(models.PortfolioProfile.user_id == user_id).first()
    if profile is None:
        profile = models.PortfolioProfile(user_id=user_id)
        db.add(profile)
        if persist:
            db.commit()
            db.refresh(profile)
    return profile


def _apply_update(instance, payload) -> None:
    for field_name, value in _dump_model_update(payload).items():
        setattr(instance, field_name, value)


def _commit_or_rollback(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="데이터 저장에 실패했습니다.") from exc


def _not_found(resource: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource}를 찾을 수 없습니다.")


def _as_date_or_none(value):
    if value is None or isinstance(value, date):
        return value
    return value


@router.get("/portfolio/profile", response_model=schemas.PortfolioProfileResponse, summary="포트폴리오 기본 정보 조회", tags=["포트폴리오"])
def get_portfolio_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    profile = _get_or_create_profile(db, _get_current_user_id(current_user), persist=True)
    return profile


@router.put("/portfolio/profile", response_model=schemas.PortfolioProfileResponse, summary="포트폴리오 기본 정보 저장/수정", tags=["포트폴리오"])
def upsert_portfolio_profile(
    payload: schemas.PortfolioProfileUpsert,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    user_id = _get_current_user_id(current_user)
    profile = _get_or_create_profile(db, user_id)
    _apply_update(profile, payload)
    _commit_or_rollback(db)
    db.refresh(profile)
    return profile


@router.post("/portfolio/profile/resume-upload", response_model=schemas.PortfolioProfileResponse, summary="기존 이력서 파일 업로드 및 AI 분석", tags=["포트폴리오"])
async def upload_resume_file(
    resume_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    filename = resume_file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_RESUME_EXTENSIONS and resume_file.content_type not in ALLOWED_RESUME_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PDF 또는 Word 파일만 업로드할 수 있습니다.",
        )

    RESUME_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    user_id = _get_current_user_id(current_user)
    safe_filename = Path(filename).name if filename else f"resume{suffix}"
    stored_name = f"user-{user_id}-{uuid4().hex}-{safe_filename}"
    stored_path = RESUME_UPLOAD_DIR / stored_name

    # 1. 파일을 서버(uploads/resumes 폴더)에 저장
    content = await resume_file.read()
    stored_path.write_bytes(content)

    # 2. 내 프로필 DB에 파일 경로 업데이트
    profile = _get_or_create_profile(db, user_id)
    profile.resume_file_url = f"{RESUME_FILE_PATH_PREFIX}/{stored_name}"

    # 3. 방금 저장한 파일을 AI에게 보내서 데이터 추출
    ai_data = extract_portfolio_data_with_ai(str(stored_path))
    
    # 4. 추출된 데이터를 각 DB 테이블에 저장
    # 4-1. 경험 (Experiences)
    for exp_data in ai_data.get("experiences", []):
        new_exp = models.PortfolioExperience(user_id=user_id, **exp_data)
        db.add(new_exp)
        
    # 4-2. 자격증 (Certifications)
    for cert_data in ai_data.get("certifications", []):
        # AI가 준 날짜 문자열("YYYY-MM-DD")을 파이썬 date 객체로 변환
        acq_date = cert_data.get("acquired_date")
        parsed_date = None
        if acq_date and isinstance(acq_date, str) and acq_date.strip():
            try:
                parsed_date = date.fromisoformat(acq_date[:10]) # YYYY-MM-DD 자르기
            except ValueError:
                pass
        cert_data["acquired_date"] = parsed_date
        new_cert = models.PortfolioCertification(user_id=user_id, **cert_data)
        db.add(new_cert)
        
    # 4-3. 어학 성적 (Languages)
    for lang_data in ai_data.get("languages", []):
        acq_date = lang_data.get("acquired_date")
        parsed_date = None
        if acq_date and isinstance(acq_date, str) and acq_date.strip():
            try:
                parsed_date = date.fromisoformat(acq_date[:10])
            except ValueError:
                pass
        lang_data["acquired_date"] = parsed_date
        new_lang = models.PortfolioLanguage(user_id=user_id, **lang_data)
        db.add(new_lang)

    # 5. DB에 모두 저장(Commit)
    _commit_or_rollback(db)
    db.refresh(profile)
    
    return profile


@router.get("/portfolio/experiences", response_model=list[schemas.PortfolioExperienceResponse], summary="경험 리스트 조회", tags=["포트폴리오"])
def list_portfolio_experiences(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    user_id = _get_current_user_id(current_user)
    return (
        db.query(models.PortfolioExperience)
        .filter(models.PortfolioExperience.user_id == user_id)
        .order_by(models.PortfolioExperience.sort_order.asc(), models.PortfolioExperience.id.asc())
        .all()
    )


@router.post("/portfolio/experiences", response_model=schemas.PortfolioExperienceResponse, summary="경험 추가하기", tags=["포트폴리오"])
def create_portfolio_experience(
    payload: schemas.PortfolioExperienceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    experience = models.PortfolioExperience(user_id=_get_current_user_id(current_user), **_dump_model(payload))
    db.add(experience)
    _commit_or_rollback(db)
    db.refresh(experience)
    return experience


@router.get("/portfolio/experiences/{experienceId}", response_model=schemas.PortfolioExperienceResponse, summary="경험 상세 조회", tags=["포트폴리오"])
def get_portfolio_experience(
    experienceId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    experience = (
        db.query(models.PortfolioExperience)
        .filter(
            models.PortfolioExperience.id == experienceId,
            models.PortfolioExperience.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if experience is None:
        raise _not_found("경험")
    return experience


@router.patch("/portfolio/experiences/{experienceId}", response_model=schemas.PortfolioExperienceResponse, summary="경험 수정", tags=["포트폴리오"])
def update_portfolio_experience(
    experienceId: int,
    payload: schemas.PortfolioExperienceUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    update_data = _dump_model_update(payload)
    if not update_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="수정할 정보를 하나 이상 제공해야 합니다.")

    experience = (
        db.query(models.PortfolioExperience)
        .filter(
            models.PortfolioExperience.id == experienceId,
            models.PortfolioExperience.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if experience is None:
        raise _not_found("경험")

    for field_name, value in update_data.items():
        setattr(experience, field_name, value)

    _commit_or_rollback(db)
    db.refresh(experience)
    return experience


@router.delete("/portfolio/experiences/{experienceId}", status_code=status.HTTP_204_NO_CONTENT, summary="경험 삭제", tags=["포트폴리오"])
def delete_portfolio_experience(
    experienceId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    experience = (
        db.query(models.PortfolioExperience)
        .filter(
            models.PortfolioExperience.id == experienceId,
            models.PortfolioExperience.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if experience is None:
        raise _not_found("경험")

    db.delete(experience)
    _commit_or_rollback(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/portfolio", response_model=schemas.PortfolioSaveResponse, summary="포트폴리오 전체 저장", tags=["포트폴리오"])
def save_full_portfolio(
    payload: schemas.PortfolioSaveRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    user_id = _get_current_user_id(current_user)
    profile = _get_or_create_profile(db, user_id)
    _apply_update(profile, payload.profile)

    db.query(models.PortfolioExperience).filter(models.PortfolioExperience.user_id == user_id).delete(synchronize_session=False)
    db.query(models.PortfolioCertification).filter(models.PortfolioCertification.user_id == user_id).delete(synchronize_session=False)
    db.query(models.PortfolioLanguage).filter(models.PortfolioLanguage.user_id == user_id).delete(synchronize_session=False)

    experiences = []
    for index, item in enumerate(payload.experiences):
        experience = models.PortfolioExperience(user_id=user_id, **_dump_model(item))
        if experience.sort_order is None:
            experience.sort_order = index
        db.add(experience)
        experiences.append(experience)

    certifications = []
    for item in payload.certifications:
        certification_data = _dump_model(item)
        certification_data["acquired_date"] = _as_date_or_none(certification_data.get("acquired_date"))
        certification = models.PortfolioCertification(user_id=user_id, **certification_data)
        db.add(certification)
        certifications.append(certification)

    languages = []
    for item in payload.languages:
        language_data = _dump_model(item)
        language_data["acquired_date"] = _as_date_or_none(language_data.get("acquired_date"))
        language = models.PortfolioLanguage(user_id=user_id, **language_data)
        db.add(language)
        languages.append(language)

    _commit_or_rollback(db)
    db.refresh(profile)

    persisted_experiences = (
        db.query(models.PortfolioExperience)
        .filter(models.PortfolioExperience.user_id == user_id)
        .order_by(models.PortfolioExperience.sort_order.asc(), models.PortfolioExperience.id.asc())
        .all()
    )
    persisted_certifications = (
        db.query(models.PortfolioCertification)
        .filter(models.PortfolioCertification.user_id == user_id)
        .order_by(models.PortfolioCertification.id.asc())
        .all()
    )
    persisted_languages = (
        db.query(models.PortfolioLanguage)
        .filter(models.PortfolioLanguage.user_id == user_id)
        .order_by(models.PortfolioLanguage.id.asc())
        .all()
    )

    return {
        "profile": profile,
        "experiences": persisted_experiences,
        "certifications": persisted_certifications,
        "languages": persisted_languages,
    }


@router.get("/portfolio/certifications", response_model=list[schemas.PortfolioCertificationResponse], summary="자격증 리스트 조회", tags=["포트폴리오"])
def list_certifications(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return (
        db.query(models.PortfolioCertification)
        .filter(models.PortfolioCertification.user_id == _get_current_user_id(current_user))
        .order_by(models.PortfolioCertification.id.asc())
        .all()
    )


@router.post("/portfolio/certifications", response_model=schemas.PortfolioCertificationResponse, summary="자격증 추가", tags=["포트폴리오"])
def create_certification(
    payload: schemas.PortfolioCertificationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    certification = models.PortfolioCertification(
        user_id=_get_current_user_id(current_user),
        **{**_dump_model(payload), "acquired_date": _as_date_or_none(_dump_model(payload).get("acquired_date"))},
    )
    db.add(certification)
    _commit_or_rollback(db)
    db.refresh(certification)
    return certification


@router.get("/portfolio/certifications/{certificationId}", response_model=schemas.PortfolioCertificationResponse, summary="자격증 상세 조회", tags=["포트폴리오"])
def get_certification(
    certificationId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    certification = (
        db.query(models.PortfolioCertification)
        .filter(
            models.PortfolioCertification.id == certificationId,
            models.PortfolioCertification.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if certification is None:
        raise _not_found("자격증")
    return certification


@router.patch("/portfolio/certifications/{certificationId}", response_model=schemas.PortfolioCertificationResponse, summary="자격증 수정", tags=["포트폴리오"])
def update_certification(
    certificationId: int,
    payload: schemas.PortfolioCertificationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    update_data = _dump_model_update(payload)
    if not update_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="수정할 정보를 하나 이상 제공해야 합니다.")

    certification = (
        db.query(models.PortfolioCertification)
        .filter(
            models.PortfolioCertification.id == certificationId,
            models.PortfolioCertification.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if certification is None:
        raise _not_found("자격증")

    for field_name, value in update_data.items():
        setattr(certification, field_name, _as_date_or_none(value) if field_name == "acquired_date" else value)

    _commit_or_rollback(db)
    db.refresh(certification)
    return certification


@router.delete("/portfolio/certifications/{certificationId}", status_code=status.HTTP_204_NO_CONTENT, summary="자격증 삭제", tags=["포트폴리오"])
def delete_certification(
    certificationId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    certification = (
        db.query(models.PortfolioCertification)
        .filter(
            models.PortfolioCertification.id == certificationId,
            models.PortfolioCertification.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if certification is None:
        raise _not_found("자격증")

    db.delete(certification)
    _commit_or_rollback(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/portfolio/languages", response_model=list[schemas.PortfolioLanguageResponse], summary="어학 성적 리스트 조회", tags=["포트폴리오"])
def list_languages(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return (
        db.query(models.PortfolioLanguage)
        .filter(models.PortfolioLanguage.user_id == _get_current_user_id(current_user))
        .order_by(models.PortfolioLanguage.id.asc())
        .all()
    )


@router.post("/portfolio/languages", response_model=schemas.PortfolioLanguageResponse, summary="어학 성적 추가", tags=["포트폴리오"])
def create_language(
    payload: schemas.PortfolioLanguageCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    language_data = _dump_model(payload)
    language_data["acquired_date"] = _as_date_or_none(language_data.get("acquired_date"))
    language = models.PortfolioLanguage(user_id=_get_current_user_id(current_user), **language_data)
    db.add(language)
    _commit_or_rollback(db)
    db.refresh(language)
    return language


@router.get("/portfolio/languages/{languageId}", response_model=schemas.PortfolioLanguageResponse, summary="어학 성적 상세 조회", tags=["포트폴리오"])
def get_language(
    languageId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    language = (
        db.query(models.PortfolioLanguage)
        .filter(
            models.PortfolioLanguage.id == languageId,
            models.PortfolioLanguage.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if language is None:
        raise _not_found("어학 성적")
    return language


@router.patch("/portfolio/languages/{languageId}", response_model=schemas.PortfolioLanguageResponse, summary="어학 성적 수정", tags=["포트폴리오"])
def update_language(
    languageId: int,
    payload: schemas.PortfolioLanguageUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    update_data = _dump_model_update(payload)
    if not update_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="수정할 정보를 하나 이상 제공해야 합니다.")

    language = (
        db.query(models.PortfolioLanguage)
        .filter(
            models.PortfolioLanguage.id == languageId,
            models.PortfolioLanguage.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if language is None:
        raise _not_found("어학 성적")

    for field_name, value in update_data.items():
        setattr(language, field_name, _as_date_or_none(value) if field_name == "acquired_date" else value)

    _commit_or_rollback(db)
    db.refresh(language)
    return language


@router.delete("/portfolio/languages/{languageId}", status_code=status.HTTP_204_NO_CONTENT, summary="어학 성적 삭제", tags=["포트폴리오"])
def delete_language(
    languageId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    language = (
        db.query(models.PortfolioLanguage)
        .filter(
            models.PortfolioLanguage.id == languageId,
            models.PortfolioLanguage.user_id == _get_current_user_id(current_user),
        )
        .first()
    )
    if language is None:
        raise _not_found("어학 성적")

    db.delete(language)
    _commit_or_rollback(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/companies", response_model=list[schemas.CompanyResponse], summary="목표 기업 목록 조회", tags=["기업"])
def list_companies(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return (
        db.query(models.Company)
        .filter(models.Company.user_id == _get_current_user_id(current_user))
        .order_by(models.Company.id.asc())
        .all()
    )


@router.post("/companies", response_model=schemas.CompanyResponse, summary="목표 기업 추가", tags=["기업"])
def create_company(
    payload: schemas.CompanyCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    company = models.Company(user_id=_get_current_user_id(current_user), **_dump_model(payload))
    db.add(company)
    _commit_or_rollback(db)
    db.refresh(company)
    return company


@router.get("/companies/{companyId}", response_model=schemas.CompanyResponse, summary="목표 기업 상세 조회", tags=["기업"])
def get_company(
    companyId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    company = (
        db.query(models.Company)
        .filter(models.Company.id == companyId, models.Company.user_id == _get_current_user_id(current_user))
        .first()
    )
    if company is None:
        raise _not_found("기업")
    return company


@router.patch("/companies/{companyId}", response_model=schemas.CompanyResponse, summary="목표 기업 수정", tags=["기업"])
def update_company(
    companyId: int,
    payload: schemas.CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    update_data = _dump_model_update(payload)
    if not update_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="수정할 정보를 하나 이상 제공해야 합니다.")

    company = (
        db.query(models.Company)
        .filter(models.Company.id == companyId, models.Company.user_id == _get_current_user_id(current_user))
        .first()
    )
    if company is None:
        raise _not_found("기업")

    for field_name, value in update_data.items():
        setattr(company, field_name, value)

    _commit_or_rollback(db)
    db.refresh(company)
    return company


@router.delete("/companies/{companyId}", status_code=status.HTTP_204_NO_CONTENT, summary="목표 기업 삭제", tags=["기업"])
def delete_company(
    companyId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    company = (
        db.query(models.Company)
        .filter(models.Company.id == companyId, models.Company.user_id == _get_current_user_id(current_user))
        .first()
    )
    if company is None:
        raise _not_found("기업")

    db.delete(company)
    _commit_or_rollback(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/resumes/{resumeId}/status", response_model=schemas.GeneratedResumeStatusResponse, summary="자소서 생성 상태 조회", tags=["자소서"])
def get_generated_resume_status(
    resumeId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    resume = (
        db.query(models.GeneratedResume)
        .filter(models.GeneratedResume.id == resumeId, models.GeneratedResume.user_id == _get_current_user_id(current_user))
        .first()
    )
    if resume is None:
        raise _not_found("자소서")
    return resume


@router.get("/resumes/companies", response_model=list[schemas.ResumeCompanyOption], summary="생성 가능한 기업 목록 조회", tags=["자소서"])
def list_resume_companies(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return (
        db.query(models.Company)
        .filter(models.Company.user_id == _get_current_user_id(current_user))
        .order_by(models.Company.id.asc())
        .all()
    )


@router.get("/resumes", response_model=list[schemas.GeneratedResumeResponse], summary="생성된 자소서 목록 조회", tags=["자소서"])
def list_generated_resumes(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return (
        db.query(models.GeneratedResume)
        .filter(models.GeneratedResume.user_id == _get_current_user_id(current_user))
        .order_by(models.GeneratedResume.id.desc())
        .all()
    )


@router.get("/resumes/{resumeId}", response_model=schemas.GeneratedResumeResponse, summary="생성된 자소서 상세 조회", tags=["자소서"])
def get_generated_resume(
    resumeId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    resume = (
        db.query(models.GeneratedResume)
        .filter(models.GeneratedResume.id == resumeId, models.GeneratedResume.user_id == _get_current_user_id(current_user))
        .first()
    )
    if resume is None:
        raise _not_found("자소서")
    return resume


@router.delete("/resumes/{resumeId}", status_code=status.HTTP_204_NO_CONTENT, summary="생성된 자소서 삭제", tags=["자소서"])
def delete_generated_resume(
    resumeId: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    resume = (
        db.query(models.GeneratedResume)
        .filter(models.GeneratedResume.id == resumeId, models.GeneratedResume.user_id == _get_current_user_id(current_user))
        .first()
    )
    if resume is None:
        raise _not_found("자소서")

    db.delete(resume)
    _commit_or_rollback(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post("/resumes/generate", response_model=schemas.GeneratedResumeResponse, summary="자소서 생성 요청", tags=["자소서"])
def generate_resume(payload: schemas.GenerateResumeRequest, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):

    user_id = _get_current_user_id(current_user)
    
    # 1. DB에서 지원하고자 하는 타겟 목표 기업 정보 로드
    company = db.query(models.Company).filter(models.Company.id == payload.company_id, models.Company.user_id == user_id).first()
    if not company:
        raise _not_found("기업 정보를 찾을 수 없습니다.")
        
    # 2. DB에서 사용자의 기본 프로필 및 경험 데이터 로드
    profile = _get_or_create_profile(db, user_id)
    experiences = db.query(models.PortfolioExperience).filter(models.PortfolioExperience.user_id == user_id).all()
    
    # 세션 충돌 방지용 동기화 작업(merge) 수행
    profile = db.merge(profile)
    company = db.merge(company)
    experiences = [db.merge(exp) for exp in experiences]
    
    # 3. 객체 데이터 파이썬 딕셔너리로 컴팩트하게 정리
    profile_data = _dump_model(profile)
    exp_data = [_dump_model(exp) for exp in experiences]
    company_data = _dump_model(company)
    
    # 4. Gemini AI 자소서 생성 서비스 엔진 호출
    ai_result = generate_masterpiece_resume(
        profile_data=profile_data,
        experiences=exp_data,
        company_data=company_data,
        additional_prompt=payload.additional_prompt or ""
    )
    
    # 5. 작성 포인트와 예상 질문을 하단에 마크다운으로 합성
    final_markdown = ai_result.get("content_markdown", "")
    final_markdown += "\n\n---\n"
    final_markdown += f"### 💡 AI 작성 포인트\n{ai_result.get('reasoning', '')}\n\n"
    final_markdown += f"### 🎯 강조된 기업 핵심 키워드\n{', '.join(ai_result.get('enhanced_keywords', []))}\n\n"
    final_markdown += "### 🎤 예상 면접 꼬리질문\n"
    for idx, question in enumerate(ai_result.get("interview_questions", [])):
        final_markdown += f"{idx + 1}. {question}\n"
    
    # 6. 완성된 자소서를 DB 테이블에 영구 저장 및 반환
    new_resume = models.GeneratedResume(
        user_id=user_id,
        company_id=company.id,
        title=ai_result.get("title", f"{company.name} 지원 자기소개서"),
        content_markdown=final_markdown,
        additional_prompt=payload.additional_prompt,
        status="COMPLETED"
    )
    
    db.add(new_resume)
    _commit_or_rollback(db)
    db.refresh(new_resume)
    
    return new_resume