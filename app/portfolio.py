from pathlib import Path
from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import auth, models, schemas
from app.database import get_db

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
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


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


@router.get("/portfolio/profile", response_model=schemas.PortfolioProfileResponse, summary="포트폴리오 기본 정보 조회", tags=["Portfolio"])
def get_portfolio_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    profile = _get_or_create_profile(db, _get_current_user_id(current_user), persist=True)
    return profile


@router.put("/portfolio/profile", response_model=schemas.PortfolioProfileResponse, summary="포트폴리오 기본 정보 저장/수정", tags=["Portfolio"])
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


@router.post("/portfolio/profile/resume-upload", response_model=schemas.PortfolioProfileResponse, summary="기존 이력서 파일 업로드", tags=["Portfolio"])
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

    content = await resume_file.read()
    stored_path.write_bytes(content)

    profile = _get_or_create_profile(db, user_id)
    profile.resume_file_url = f"{RESUME_FILE_PATH_PREFIX}/{stored_name}"
    _commit_or_rollback(db)
    db.refresh(profile)
    return profile


@router.get("/portfolio/experiences", response_model=list[schemas.PortfolioExperienceResponse], summary="경험 리스트 조회", tags=["Portfolio"])
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


@router.post("/portfolio/experiences", response_model=schemas.PortfolioExperienceResponse, summary="경험 추가하기", tags=["Portfolio"])
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


@router.get("/portfolio/experiences/{experienceId}", response_model=schemas.PortfolioExperienceResponse, summary="경험 상세 조회", tags=["Portfolio"])
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


@router.patch("/portfolio/experiences/{experienceId}", response_model=schemas.PortfolioExperienceResponse, summary="경험 수정", tags=["Portfolio"])
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


@router.delete("/portfolio/experiences/{experienceId}", status_code=status.HTTP_204_NO_CONTENT, summary="경험 삭제", tags=["Portfolio"])
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


@router.put("/portfolio", response_model=schemas.PortfolioSaveResponse, summary="포트폴리오 전체 저장", tags=["Portfolio"])
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


@router.get("/portfolio/certifications", response_model=list[schemas.PortfolioCertificationResponse], summary="자격증 리스트 조회", tags=["Portfolio"])
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


@router.post("/portfolio/certifications", response_model=schemas.PortfolioCertificationResponse, summary="자격증 추가", tags=["Portfolio"])
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


@router.get("/portfolio/certifications/{certificationId}", response_model=schemas.PortfolioCertificationResponse, summary="자격증 상세 조회", tags=["Portfolio"])
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


@router.patch("/portfolio/certifications/{certificationId}", response_model=schemas.PortfolioCertificationResponse, summary="자격증 수정", tags=["Portfolio"])
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


@router.delete("/portfolio/certifications/{certificationId}", status_code=status.HTTP_204_NO_CONTENT, summary="자격증 삭제", tags=["Portfolio"])
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


@router.get("/portfolio/languages", response_model=list[schemas.PortfolioLanguageResponse], summary="어학 성적 리스트 조회", tags=["Portfolio"])
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


@router.post("/portfolio/languages", response_model=schemas.PortfolioLanguageResponse, summary="어학 성적 추가", tags=["Portfolio"])
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


@router.get("/portfolio/languages/{languageId}", response_model=schemas.PortfolioLanguageResponse, summary="어학 성적 상세 조회", tags=["Portfolio"])
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


@router.patch("/portfolio/languages/{languageId}", response_model=schemas.PortfolioLanguageResponse, summary="어학 성적 수정", tags=["Portfolio"])
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


@router.delete("/portfolio/languages/{languageId}", status_code=status.HTTP_204_NO_CONTENT, summary="어학 성적 삭제", tags=["Portfolio"])
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


@router.get("/companies", response_model=list[schemas.CompanyResponse], summary="목표 기업 목록 조회", tags=["Company"])
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


@router.post("/companies", response_model=schemas.CompanyResponse, summary="목표 기업 추가", tags=["Company"])
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


@router.get("/companies/{companyId}", response_model=schemas.CompanyResponse, summary="목표 기업 상세 조회", tags=["Company"])
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


@router.patch("/companies/{companyId}", response_model=schemas.CompanyResponse, summary="목표 기업 수정", tags=["Company"])
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


@router.delete("/companies/{companyId}", status_code=status.HTTP_204_NO_CONTENT, summary="목표 기업 삭제", tags=["Company"])
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


@router.post("/resumes/generate", status_code=status.HTTP_501_NOT_IMPLEMENTED, summary="자소서 생성 요청", tags=["Resume"])
def generate_resume(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="AI 자소서 생성은 아직 준비되지 않았습니다.",
    )


@router.get("/resumes/{resumeId}/status", response_model=schemas.GeneratedResumeStatusResponse, summary="자소서 생성 상태 조회", tags=["Resume"])
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


@router.get("/resumes/companies", response_model=list[schemas.ResumeCompanyOption], summary="생성 가능한 기업 목록 조회", tags=["Resume"])
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


@router.get("/resumes", response_model=list[schemas.GeneratedResumeResponse], summary="생성된 자소서 목록 조회", tags=["Resume"])
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


@router.get("/resumes/{resumeId}", response_model=schemas.GeneratedResumeResponse, summary="생성된 자소서 상세 조회", tags=["Resume"])
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


@router.delete("/resumes/{resumeId}", status_code=status.HTTP_204_NO_CONTENT, summary="생성된 자소서 삭제", tags=["Resume"])
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
