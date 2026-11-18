import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text,
    DateTime, ForeignKey, Enum as SQLEnum, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.session import Base

def utcnow():
    return datetime.now(timezone.utc)

# --- Enums ---
class AccountStatusEnum(str, enum.Enum):
    active = "active"
    deactivated = "deactivated"

class CodePurposeEnum(str, enum.Enum):
    signup = "signup"
    google_login = "google_login"
    password_reset = "password_reset"

class SexEnum(str, enum.Enum):
    Male = "Male"
    Female = "Female"
    Other = "Other"

class AssessmentStatusEnum(str, enum.Enum):
    draft = "draft"
    images_uploaded = "images_uploaded"
    analyzing = "analyzing"
    completed = "completed"
    failed = "failed"

class SurfaceTypeEnum(str, enum.Enum):
    oral_cavity = "oral_cavity"
    fingernails = "fingernails"
    conjunctiva = "conjunctiva"
    tongue = "tongue"
    palm = "palm"

class VerdictStatusEnum(str, enum.Enum):
    Confirm = "Confirm"
    Reject = "Reject"

# ==============================================================================
# ACCOUNT & SUPPORT TABLES (Figure 6)
# ==============================================================================

class Clinician(Base):
    __tablename__ = "clinicians"

    clinician_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String(120), nullable=False)
    email = Column(String(150), unique=True, nullable=False, index=True)
    email_verified = Column(Boolean, default=False, nullable=False)
    password_hash = Column(String(255), nullable=True)  # Null for Google-only users
    google_id = Column(String(255), unique=True, nullable=True)
    failed_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    push_enabled = Column(Boolean, default=True, nullable=False)
    status = Column(SQLEnum(AccountStatusEnum), default=AccountStatusEnum.active, nullable=False)
    deactivated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    patients = relationship("Patient", back_populates="creator", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="clinician")
    verification_codes = relationship("VerificationCode", back_populates="clinician", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="clinician", cascade="all, delete-orphan")
    device_tokens = relationship("DeviceToken", back_populates="clinician", cascade="all, delete-orphan")
    feedback_reports = relationship("FeedbackReport", back_populates="clinician", cascade="all, delete-orphan")


class VerificationCode(Base):
    __tablename__ = "verification_codes"

    code_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clinician_id = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="CASCADE"), nullable=False)
    purpose = Column(SQLEnum(CodePurposeEnum), nullable=False)
    code_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)

    clinician = relationship("Clinician", back_populates="verification_codes")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    token_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clinician_id = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(255), unique=True, nullable=False)
    device_name = Column(String(120), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    clinician = relationship("Clinician", back_populates="refresh_tokens")


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    device_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clinician_id = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="CASCADE"), nullable=False)
    push_token = Column(String(512), nullable=False)
    platform = Column(String(30), nullable=False)  # android / ios
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    clinician = relationship("Clinician", back_populates="device_tokens")


class FeedbackReport(Base):
    __tablename__ = "feedback_reports"

    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clinician_id = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="CASCADE"), nullable=False)
    message = Column(Text, nullable=False)
    screenshot_key = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    clinician = relationship("Clinician", back_populates="feedback_reports")


# ==============================================================================
# CLINICAL TABLES (Figure 5)
# ==============================================================================

class Patient(Base):
    __tablename__ = "patients"

    patient_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="CASCADE"), nullable=False, index=True)
    patient_code = Column(String(6), unique=True, nullable=False, index=True)  # 6-char random code
    full_name = Column(Text, nullable=False)  # AES-256 encrypted
    age = Column(Integer, nullable=False)
    sex = Column(SQLEnum(SexEnum), nullable=False)

    creator = relationship("Clinician", back_populates="patients")
    assessments = relationship("Assessment", back_populates="patient", cascade="all, delete-orphan")


class Assessment(Base):
    __tablename__ = "assessments"

    assessment_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False, index=True)
    clinician_id = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="RESTRICT"), nullable=False, index=True)
    session_code = Column(String(25), unique=True, nullable=False, index=True)  # PAT-YYYYMMDD-XXX
    status = Column(SQLEnum(AssessmentStatusEnum), default=AssessmentStatusEnum.draft, nullable=False)
    consent_given = Column(Boolean, default=False, nullable=False)
    consent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    hidden_at = Column(DateTime(timezone=True), nullable=True)  # Soft-hide on delete

    patient = relationship("Patient", back_populates="assessments")
    clinician = relationship("Clinician", back_populates="assessments")
    surface_images = relationship("SurfaceImage", back_populates="assessment", cascade="all, delete-orphan")
    ai_result = relationship("AIScreeningResult", back_populates="assessment", uselist=False, cascade="all, delete-orphan")
    verdict = relationship("ClinicianVerdict", back_populates="assessment", uselist=False, cascade="all, delete-orphan")


class SurfaceImage(Base):
    __tablename__ = "surface_images"
    __table_args__ = (
        UniqueConstraint("assessment_id", "surface_type", name="uq_assessment_surface"),
    )

    image_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("assessments.assessment_id", ondelete="CASCADE"), nullable=False, index=True)
    surface_type = Column(SQLEnum(SurfaceTypeEnum), nullable=False)
    storage_key = Column(String(255), nullable=False)
    lighting_score = Column(Float, nullable=True)
    blur_score = Column(Float, nullable=True)
    quality_passed = Column(Boolean, default=True, nullable=False)
    uncertainty = Column(Text, nullable=True)  # AES-256 encrypted per-surface uncertainty

    assessment = relationship("Assessment", back_populates="surface_images")
    heatmap = relationship("ImageHeatmap", back_populates="surface_image", uselist=False, cascade="all, delete-orphan")


class AIScreeningResult(Base):
    __tablename__ = "ai_screening_results"

    result_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("assessments.assessment_id", ondelete="CASCADE"), unique=True, nullable=False)
    model_version = Column(String(50), nullable=False)
    risk_level = Column(Text, nullable=False)       # AES-256 encrypted (Low/Moderate/High)
    uncertainty = Column(Text, nullable=False)      # AES-256 encrypted
    needs_referral = Column(Text, nullable=False)   # AES-256 encrypted

    assessment = relationship("Assessment", back_populates="ai_result")
    predictions = relationship("ConditionPrediction", back_populates="result", cascade="all, delete-orphan")
    heatmaps = relationship("ImageHeatmap", back_populates="result", cascade="all, delete-orphan")


class ConditionPrediction(Base):
    __tablename__ = "condition_predictions"

    prediction_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id = Column(UUID(as_uuid=True), ForeignKey("ai_screening_results.result_id", ondelete="CASCADE"), nullable=False, index=True)
    condition_code = Column(String(50), nullable=False)
    risk_score = Column(Text, nullable=False)   # AES-256 encrypted
    confidence = Column(Text, nullable=False)   # AES-256 encrypted

    result = relationship("AIScreeningResult", back_populates="predictions")


class ImageHeatmap(Base):
    __tablename__ = "image_heatmaps"

    heatmap_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id = Column(UUID(as_uuid=True), ForeignKey("ai_screening_results.result_id", ondelete="CASCADE"), nullable=False)
    image_id = Column(UUID(as_uuid=True), ForeignKey("surface_images.image_id", ondelete="CASCADE"), unique=True, nullable=False)
    heatmap_storage_key = Column(String(255), nullable=False)
    surface_contribution = Column(Float, nullable=True)

    result = relationship("AIScreeningResult", back_populates="heatmaps")
    surface_image = relationship("SurfaceImage", back_populates="heatmap")


class ClinicianVerdict(Base):
    __tablename__ = "clinician_verdicts"

    verdict_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("assessments.assessment_id", ondelete="CASCADE"), unique=True, nullable=False)
    clinician_id = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="RESTRICT"), nullable=False)
    verdict_status = Column(SQLEnum(VerdictStatusEnum), nullable=False)
    comment = Column(Text, nullable=True)  # AES-256 encrypted (required >=10 chars on Reject)
    decided_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    assessment = relationship("Assessment", back_populates="verdict")


class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clinician_id = Column(UUID(as_uuid=True), ForeignKey("clinicians.clinician_id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
