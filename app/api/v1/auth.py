import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    hash_password,
    verify_password,
    hash_token_or_code,
    create_access_token,
)
from app.core.deps import get_current_clinician
from app.db.session import get_db
from app.db.models import (
    Clinician,
    VerificationCode,
    RefreshToken,
    AuditLog,
    AccountStatusEnum,
    CodePurposeEnum,
)
from app.services.email_sender import get_email_sender, EmailSender
from app.api.v1.schemas_auth import (
    SignupRequest,
    VerifyEmailRequest,
    ResendCodeRequest,
    LoginRequest,
    RefreshRequest,
    LogoutRequest,
    TokenResponse,
    ClinicianProfileResponse,
)

router = APIRouter(prefix="/v1", tags=["Authentication & Profile"])

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _log_audit(db: Session, clinician_id, action: str, entity_type: str, entity_id: str = None):
    log = AuditLog(
        clinician_id=clinician_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
    )
    db.add(log)

def _issue_6_digit_code(db: Session, clinician: Clinician, purpose: CodePurposeEnum, email_sender: EmailSender):
    raw_code = f"{secrets.randbelow(1000000):06d}"
    vc = VerificationCode(
        clinician_id=clinician.clinician_id,
        purpose=purpose,
        code_hash=hash_token_or_code(raw_code),
        expires_at=_utcnow() + timedelta(minutes=10),
    )
    db.add(vc)
    email_sender.send_verification_code(clinician.email, raw_code, purpose.value)

@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
):
    existing = db.query(Clinician).filter(Clinician.email == payload.email.lower()).first()
    if existing:
        if existing.email_verified:
            raise HTTPException(status_code=400, detail="Email is already registered.")
        existing.full_name = payload.full_name
        existing.password_hash = hash_password(payload.password)
        _issue_6_digit_code(db, existing, CodePurposeEnum.signup, email_sender)
        db.commit()
        return {"message": "Verification code sent to email.", "email": existing.email}

    clinician = Clinician(
        full_name=payload.full_name,
        email=payload.email.lower(),
        email_verified=False,
        password_hash=hash_password(payload.password),
    )
    db.add(clinician)
    db.flush()

    _issue_6_digit_code(db, clinician, CodePurposeEnum.signup, email_sender)
    _log_audit(db, clinician.clinician_id, "CLINICIAN_SIGNUP", "clinicians", clinician.clinician_id)
    db.commit()

    return {"message": "Account created. Please enter the 6-digit verification code sent to your email.", "email": clinician.email}

@router.post("/auth/verify-email")
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    clinician = db.query(Clinician).filter(Clinician.email == payload.email.lower()).first()
    if not clinician:
        raise HTTPException(status_code=404, detail="Account not found.")

    vc = (
        db.query(VerificationCode)
        .filter(
            VerificationCode.clinician_id == clinician.clinician_id,
            VerificationCode.used_at.is_(None),
        )
        .order_by(VerificationCode.expires_at.desc())
        .first()
    )

    if not vc or vc.expires_at < _utcnow():
        raise HTTPException(status_code=400, detail="Verification code expired or invalid.")

    if vc.attempts >= 5:
        raise HTTPException(status_code=429, detail="Too many invalid attempts. Request a new code.")

    if vc.code_hash != hash_token_or_code(payload.code):
        vc.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="Incorrect 6-digit verification code.")

    vc.used_at = _utcnow()
    clinician.email_verified = True
    _log_audit(db, clinician.clinician_id, "EMAIL_VERIFIED", "clinicians", clinician.clinician_id)
    db.commit()

    return {"message": "Email verified successfully. You can now log in."}

@router.post("/auth/resend-code")
def resend_code(
    payload: ResendCodeRequest,
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
):
    clinician = db.query(Clinician).filter(Clinician.email == payload.email.lower()).first()
    if not clinician:
        raise HTTPException(status_code=404, detail="Account not found.")

    latest_vc = (
        db.query(VerificationCode)
        .filter(VerificationCode.clinician_id == clinician.clinician_id)
        .order_by(VerificationCode.expires_at.desc())
        .first()
    )
    if latest_vc:
        issued_at = latest_vc.expires_at - timedelta(minutes=10)
        if (_utcnow() - issued_at).total_seconds() < 60:
            raise HTTPException(status_code=429, detail="Please wait 60 seconds before requesting a new code.")

    _issue_6_digit_code(db, clinician, CodePurposeEnum.signup, email_sender)
    db.commit()
    return {"message": "A new 6-digit verification code has been sent."}

@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    clinician = db.query(Clinician).filter(Clinician.email == payload.email.lower()).first()
    if not clinician or not clinician.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if clinician.locked_until and clinician.locked_until > _utcnow():
        remaining = int((clinician.locked_until - _utcnow()).total_seconds())
        raise HTTPException(
            status_code=423,
            detail=f"Account temporarily locked due to 3 failed attempts. Try again in {remaining} seconds.",
        )

    if not verify_password(payload.password, clinician.password_hash):
        clinician.failed_attempts += 1
        if clinician.failed_attempts >= 3:
            clinician.locked_until = _utcnow() + timedelta(minutes=2)
            clinician.failed_attempts = 0
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not clinician.email_verified:
        raise HTTPException(status_code=403, detail="Email not verified. Please verify your 6-digit code first.")

    clinician.failed_attempts = 0
    clinician.locked_until = None
    if clinician.status == AccountStatusEnum.deactivated:
        clinician.status = AccountStatusEnum.active
        clinician.deactivated_at = None

    access_token = create_access_token(clinician.clinician_id)
    raw_refresh = secrets.token_urlsafe(48)
    refresh_row = RefreshToken(
        clinician_id=clinician.clinician_id,
        token_hash=hash_token_or_code(raw_refresh),
        device_name=payload.device_name,
        expires_at=_utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(refresh_row)
    _log_audit(db, clinician.clinician_id, "CLINICIAN_LOGIN", "clinicians", clinician.clinician_id)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_session(payload: RefreshRequest, db: Session = Depends(get_db)):
    token_hash = hash_token_or_code(payload.refresh_token)
    rt = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()

    if not rt or rt.revoked_at is not None or rt.expires_at < _utcnow():
        raise HTTPException(status_code=401, detail="Session expired, please log in again.")

    access_token = create_access_token(rt.clinician_id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=payload.refresh_token,
        expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@router.post("/auth/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    token_hash = hash_token_or_code(payload.refresh_token)
    rt = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if rt and rt.revoked_at is None:
        rt.revoked_at = _utcnow()
        _log_audit(db, rt.clinician_id, "CLINICIAN_LOGOUT", "clinicians", rt.clinician_id)
        db.commit()
    return {"message": "Logged out successfully."}

@router.get("/me", response_model=ClinicianProfileResponse)
def get_my_profile(current_clinician: Clinician = Depends(get_current_clinician)):
    return current_clinician
