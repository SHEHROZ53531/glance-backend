import uuid
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_clinician
from app.core.security import encrypt_field, decrypt_field
from app.db.session import get_db, SessionLocal
from app.db.models import (
    Clinician,
    Assessment,
    AIScreeningResult,
    ConditionPrediction,
    ImageHeatmap,
    ClinicianVerdict,
    AssessmentStatusEnum,
    VerdictStatusEnum,
    AuditLog,
)
from app.storage.image_storage import get_image_storage, ImageStorage
from app.services.inference_client import get_inference_client

router = APIRouter(prefix="/v1", tags=["AI Screening, History & Verdicts"])

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

# --- Schemas ---
class ConditionPredictionOut(BaseModel):
    condition_code: str
    risk_score: float
    confidence: float

class SurfaceHeatmapOut(BaseModel):
    heatmap_id: UUID
    image_id: UUID
    surface_type: str
    surface_contribution: Optional[float]
    heatmap_url: str

class AIResultOut(BaseModel):
    result_id: UUID
    model_version: str
    risk_level: str
    uncertainty: float
    needs_referral: bool
    predictions: List[ConditionPredictionOut]
    heatmaps: List[SurfaceHeatmapOut]

class VerdictRequest(BaseModel):
    verdict_status: VerdictStatusEnum
    comment: Optional[str] = Field(None, max_length=1000)

class VerdictOut(BaseModel):
    verdict_id: UUID
    verdict_status: VerdictStatusEnum
    comment: Optional[str]
    decided_at: datetime

class AssessmentDetailOut(BaseModel):
    assessment_id: UUID
    session_code: str
    status: AssessmentStatusEnum
    patient_id: UUID
    patient_code: str
    patient_name: str
    patient_age: int
    patient_sex: str
    created_at: datetime
    ai_result: Optional[AIResultOut] = None
    verdict: Optional[VerdictOut] = None

class AssessmentHistoryItemOut(BaseModel):
    assessment_id: UUID
    session_code: str
    status: AssessmentStatusEnum
    patient_code: str
    patient_name: str
    risk_level: Optional[str] = None
    verdict_status: Optional[VerdictStatusEnum] = None
    created_at: datetime

# --- Background Worker ---
def _run_inference_job(assessment_id: UUID):
    db = SessionLocal()
    storage = get_image_storage()
    client = get_inference_client()
    try:
        assessment = db.query(Assessment).filter(Assessment.assessment_id == assessment_id).first()
        if not assessment:
            return

        surfaces_payload = {}
        surface_map = {}
        for img in assessment.surface_images:
            raw_bytes = storage.load_decrypted(img.storage_key)
            surfaces_payload[img.surface_type.value] = raw_bytes
            surface_map[img.surface_type.value] = img

        output = client.run_screening(surfaces_payload)

        if assessment.ai_result:
            db.delete(assessment.ai_result)
            db.flush()

        ai_res = AIScreeningResult(
            assessment_id=assessment.assessment_id,
            model_version=output["model_version"],
            risk_level=encrypt_field(str(output["risk_level"])),
            uncertainty=encrypt_field(str(output["uncertainty"])),
            needs_referral=encrypt_field("true" if output["needs_referral"] else "false"),
        )
        db.add(ai_res)
        db.flush()

        for pred in output["predictions"]:
            db.add(ConditionPrediction(
                result_id=ai_res.result_id,
                condition_code=pred["condition_code"],
                risk_score=encrypt_field(str(pred["risk_score"])),
                confidence=encrypt_field(str(pred["confidence"])),
            ))

        for s_name, s_data in output["surfaces"].items():
            img_row = surface_map.get(s_name)
            if not img_row:
                continue
            img_row.uncertainty = encrypt_field(str(s_data["surface_uncertainty"]))
            hm_key = f"heatmaps/{assessment.assessment_id}/{s_name}_{uuid.uuid4().hex}.enc"
            storage.save_encrypted(hm_key, s_data["heatmap_png"])

            db.add(ImageHeatmap(
                result_id=ai_res.result_id,
                image_id=img_row.image_id,
                heatmap_storage_key=hm_key,
                surface_contribution=s_data["surface_contribution"],
            ))

        assessment.status = AssessmentStatusEnum.completed
        db.commit()
    except Exception:
        db.rollback()
        failed_a = db.query(Assessment).filter(Assessment.assessment_id == assessment_id).first()
        if failed_a:
            failed_a.status = AssessmentStatusEnum.failed
            db.commit()
    finally:
        db.close()

# --- Endpoints ---
@router.post("/assessments/{assessment_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
def trigger_ai_analysis(
    assessment_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
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

    if len(assessment.surface_images) == 0:
        raise HTTPException(status_code=400, detail="Capture at least one surface image before running AI analysis.")

    assessment.status = AssessmentStatusEnum.analyzing
    db.add(AuditLog(
        clinician_id=current_clinician.clinician_id,
        action="AI_ANALYSIS_STARTED",
        entity_type="assessments",
        entity_id=str(assessment.assessment_id),
    ))
    db.commit()

    background_tasks.add_task(_run_inference_job, assessment.assessment_id)
    return {
        "assessment_id": str(assessment.assessment_id),
        "status": "analyzing",
        "message": "AI screening analysis started. Poll GET /v1/assessments/{id} for results.",
    }

@router.get("/assessments/{assessment_id}", response_model=AssessmentDetailOut)
def get_assessment_detail(
    assessment_id: UUID,
    db: Session = Depends(get_db),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    a = (
        db.query(Assessment)
        .filter(
            Assessment.assessment_id == assessment_id,
            Assessment.clinician_id == current_clinician.clinician_id,
            Assessment.hidden_at.is_(None),
        )
        .first()
    )
    if not a:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    ai_out = None
    if a.ai_result:
        ai_out = AIResultOut(
            result_id=a.ai_result.result_id,
            model_version=a.ai_result.model_version,
            risk_level=decrypt_field(a.ai_result.risk_level),
            uncertainty=float(decrypt_field(a.ai_result.uncertainty)),
            needs_referral=decrypt_field(a.ai_result.needs_referral).lower() == "true",
            predictions=[
                ConditionPredictionOut(
                    condition_code=p.condition_code,
                    risk_score=float(decrypt_field(p.risk_score)),
                    confidence=float(decrypt_field(p.confidence)),
                )
                for p in a.ai_result.predictions
            ],
            heatmaps=[
                SurfaceHeatmapOut(
                    heatmap_id=hm.heatmap_id,
                    image_id=hm.image_id,
                    surface_type=hm.surface_image.surface_type.value,
                    surface_contribution=hm.surface_contribution,
                    heatmap_url=f"/v1/heatmaps/{hm.heatmap_id}",
                )
                for hm in a.ai_result.heatmaps
            ],
        )

    verdict_out = None
    if a.verdict:
        verdict_out = VerdictOut(
            verdict_id=a.verdict.verdict_id,
            verdict_status=a.verdict.verdict_status,
            comment=decrypt_field(a.verdict.comment) if a.verdict.comment else None,
            decided_at=a.verdict.decided_at,
        )

    return AssessmentDetailOut(
        assessment_id=a.assessment_id,
        session_code=a.session_code,
        status=a.status,
        patient_id=a.patient.patient_id,
        patient_code=a.patient.patient_code,
        patient_name=decrypt_field(a.patient.full_name),
        patient_age=a.patient.age,
        patient_sex=a.patient.sex.value,
        created_at=a.created_at,
        ai_result=ai_out,
        verdict=verdict_out,
    )

@router.get("/assessments", response_model=List[AssessmentHistoryItemOut])
def list_assessment_history(
    search: Optional[str] = Query(None, description="Search by session_code, patient_code, or patient_name"),
    db: Session = Depends(get_db),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    rows = (
        db.query(Assessment)
        .filter(
            Assessment.clinician_id == current_clinician.clinician_id,
            Assessment.hidden_at.is_(None),
        )
        .order_by(Assessment.created_at.desc())
        .all()
    )

    items = []
    for a in rows:
        p_name = decrypt_field(a.patient.full_name)
        r_level = decrypt_field(a.ai_result.risk_level) if a.ai_result else None
        v_status = a.verdict.verdict_status if a.verdict else None

        if search and search.strip():
            q = search.strip().lower()
            if (
                q not in a.session_code.lower()
                and q not in a.patient.patient_code.lower()
                and q not in p_name.lower()
            ):
                continue

        items.append(
            AssessmentHistoryItemOut(
                assessment_id=a.assessment_id,
                session_code=a.session_code,
                status=a.status,
                patient_code=a.patient.patient_code,
                patient_name=p_name,
                risk_level=r_level,
                verdict_status=v_status,
                created_at=a.created_at,
            )
        )
    return items

@router.post("/assessments/{assessment_id}/verdict", response_model=VerdictOut)
def submit_clinician_verdict(
    assessment_id: UUID,
    payload: VerdictRequest,
    db: Session = Depends(get_db),
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

    if assessment.status != AssessmentStatusEnum.completed:
        raise HTTPException(status_code=400, detail="AI analysis must complete before submitting a verdict.")

    clean_comment = payload.comment.strip() if payload.comment else ""
    if payload.verdict_status == VerdictStatusEnum.Reject and len(clean_comment) < 10:
        raise HTTPException(
            status_code=400,
            detail="A clinical comment of at least 10 characters is required when rejecting AI results.",
        )

    encrypted_comment = encrypt_field(clean_comment) if clean_comment else None

    if assessment.verdict:
        assessment.verdict.verdict_status = payload.verdict_status
        assessment.verdict.comment = encrypted_comment
        assessment.verdict.decided_at = _utcnow()
        verdict = assessment.verdict
    else:
        verdict = ClinicianVerdict(
            assessment_id=assessment.assessment_id,
            clinician_id=current_clinician.clinician_id,
            verdict_status=payload.verdict_status,
            comment=encrypted_comment,
        )
        db.add(verdict)

    db.add(AuditLog(
        clinician_id=current_clinician.clinician_id,
        action=f"VERDICT_{payload.verdict_status.value.upper()}",
        entity_type="clinician_verdicts",
        entity_id=str(assessment.assessment_id),
    ))
    db.commit()
    db.refresh(verdict)

    return VerdictOut(
        verdict_id=verdict.verdict_id,
        verdict_status=verdict.verdict_status,
        comment=clean_comment if clean_comment else None,
        decided_at=verdict.decided_at,
    )

@router.get("/heatmaps/{heatmap_id}")
def get_decrypted_heatmap(
    heatmap_id: UUID,
    db: Session = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    hm = db.query(ImageHeatmap).filter(ImageHeatmap.heatmap_id == heatmap_id).first()
    if not hm or hm.result.assessment.clinician_id != current_clinician.clinician_id:
        raise HTTPException(status_code=404, detail="Heatmap not found.")

    png_bytes = storage.load_decrypted(hm.heatmap_storage_key)
    return Response(content=png_bytes, media_type="image/png")
