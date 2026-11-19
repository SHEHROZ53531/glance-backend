import uuid
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_clinician
from app.core.security import decrypt_field
from app.db.session import get_db
from app.db.models import (
    Clinician,
    Patient,
    Assessment,
    SurfaceImage,
    AssessmentStatusEnum,
    SurfaceTypeEnum,
    AuditLog,
)
from app.storage.image_storage import get_image_storage, ImageStorage

router = APIRouter(prefix="/v1/assessments", tags=["Assessments & Image Capture"])

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _generate_session_code(db: Session) -> str:
    today_str = _utcnow().strftime("%Y%m%d")
    prefix = f"PAT-{today_str}-"
    count_today = (
        db.query(Assessment)
        .filter(Assessment.session_code.like(f"{prefix}%"))
        .count()
    )
    return f"{prefix}{count_today + 1:03d}"

# --- Schemas ---
class AssessmentCreateRequest(BaseModel):
    patient_id: UUID
    consent_given: bool

class SurfaceImageOut(BaseModel):
    image_id: UUID
    surface_type: SurfaceTypeEnum
    lighting_score: Optional[float]
    blur_score: Optional[float]
    quality_passed: bool

class AssessmentResponse(BaseModel):
    assessment_id: UUID
    patient_id: UUID
    patient_code: str
    patient_name: str
    session_code: str
    status: AssessmentStatusEnum
    consent_given: bool
    consent_at: Optional[datetime]
    created_at: datetime
    uploaded_surfaces: List[SurfaceImageOut] = []

def _serialize_assessment(a: Assessment) -> AssessmentResponse:
    return AssessmentResponse(
        assessment_id=a.assessment_id,
        patient_id=a.patient_id,
        patient_code=a.patient.patient_code,
        patient_name=decrypt_field(a.patient.full_name),
        session_code=a.session_code,
        status=a.status,
        consent_given=a.consent_given,
        consent_at=a.consent_at,
        created_at=a.created_at,
        uploaded_surfaces=[
            SurfaceImageOut(
                image_id=img.image_id,
                surface_type=img.surface_type,
                lighting_score=img.lighting_score,
                blur_score=img.blur_score,
                quality_passed=img.quality_passed,
            )
            for img in a.surface_images
        ],
    )

# --- Routes ---
@router.post("", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
def create_assessment(
    payload: AssessmentCreateRequest,
    db: Session = Depends(get_db),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    if not payload.consent_given:
        raise HTTPException(
            status_code=400,
            detail="Patient consent must be confirmed before creating a screening session.",
        )

    patient = (
        db.query(Patient)
        .filter(
            Patient.patient_id == payload.patient_id,
            Patient.created_by == current_clinician.clinician_id,
        )
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    session_code = _generate_session_code(db)
    assessment = Assessment(
        patient_id=patient.patient_id,
        clinician_id=current_clinician.clinician_id,
        session_code=session_code,
        status=AssessmentStatusEnum.draft,
        consent_given=True,
        consent_at=_utcnow(),
    )
    db.add(assessment)
    db.flush()

    db.add(AuditLog(
        clinician_id=current_clinician.clinician_id,
        action="ASSESSMENT_CREATED",
        entity_type="assessments",
        entity_id=str(assessment.assessment_id),
    ))
    db.commit()
    db.refresh(assessment)
    return _serialize_assessment(assessment)

@router.post("/{assessment_id}/images", response_model=SurfaceImageOut)
async def upload_surface_image(
    assessment_id: UUID,
    surface_type: SurfaceTypeEnum = Form(...),
    lighting_score: Optional[float] = Form(None),
    blur_score: Optional[float] = Form(None),
    quality_passed: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.assessment_id == assessment_id,
            Assessment.clinician_id == current_clinician.clinician_id,
            Assessment.hidden_at.is_(None),
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment session not found.")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image file is empty.")

    # Never include patient name in file path (Privacy by design)
    storage_key = f"images/{assessment.assessment_id}/{surface_type.value}_{uuid.uuid4().hex}.enc"
    storage.save_encrypted(storage_key, raw_bytes)

    # Replace existing image if clinician retakes the same surface
    existing_img = (
        db.query(SurfaceImage)
        .filter(
            SurfaceImage.assessment_id == assessment.assessment_id,
            SurfaceImage.surface_type == surface_type,
        )
        .first()
    )
    if existing_img:
        storage.delete_file(existing_img.storage_key)
        existing_img.storage_key = storage_key
        existing_img.lighting_score = lighting_score
        existing_img.blur_score = blur_score
        existing_img.quality_passed = quality_passed
        img_record = existing_img
    else:
        img_record = SurfaceImage(
            assessment_id=assessment.assessment_id,
            surface_type=surface_type,
            storage_key=storage_key,
            lighting_score=lighting_score,
            blur_score=blur_score,
            quality_passed=quality_passed,
        )
        db.add(img_record)

    assessment.status = AssessmentStatusEnum.images_uploaded
    db.commit()
    db.refresh(img_record)

    return SurfaceImageOut(
        image_id=img_record.image_id,
        surface_type=img_record.surface_type,
        lighting_score=img_record.lighting_score,
        blur_score=img_record.blur_score,
        quality_passed=img_record.quality_passed,
    )

@router.delete("/{assessment_id}")
def delete_or_hide_assessment(
    assessment_id: UUID,
    db: Session = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    assessment = (
        db.query(Assessment)
        .filter(
            Assessment.assessment_id == assessment_id,
            Assessment.clinician_id == current_clinician.clinician_id,
            Assessment.hidden_at.is_(None),
        )
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    # FR-1.44 & FR-1.51: Drafts are hard-deleted; completed assessments are hidden
    if assessment.status in (AssessmentStatusEnum.draft, AssessmentStatusEnum.images_uploaded):
        for img in assessment.surface_images:
            storage.delete_file(img.storage_key)
        db.delete(assessment)
        db.commit()
        return {"message": "Draft assessment permanently deleted."}
    else:
        assessment.hidden_at = _utcnow()
        db.commit()
        return {"message": "Completed assessment removed from your view."}
