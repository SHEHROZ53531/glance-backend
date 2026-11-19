import secrets
from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_clinician
from app.core.security import encrypt_field, decrypt_field
from app.db.session import get_db
from app.db.models import Clinician, Patient, SexEnum, AuditLog

router = APIRouter(prefix="/v1/patients", tags=["Patients"])

# Exclude look-alike characters 0, O, 1, I, L (Architecture Doc Section 7.1)
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

def _generate_unique_patient_code(db: Session) -> str:
    for _ in range(15):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        exists = db.query(Patient).filter(Patient.patient_code == code).first()
        if not exists:
            return code
    raise HTTPException(status_code=500, detail="Unable to generate unique patient code.")

# --- Schemas ---
class PatientCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    age: int = Field(..., ge=1, le=120)
    sex: SexEnum

class PatientUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    age: Optional[int] = Field(None, ge=1, le=120)
    sex: Optional[SexEnum] = None

class PatientResponse(BaseModel):
    patient_id: UUID
    patient_code: str
    full_name: str
    age: int
    sex: SexEnum

def _to_patient_response(p: Patient) -> PatientResponse:
    return PatientResponse(
        patient_id=p.patient_id,
        patient_code=p.patient_code,
        full_name=decrypt_field(p.full_name),
        age=p.age,
        sex=p.sex,
    )

# --- Routes ---
@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def create_patient(
    payload: PatientCreateRequest,
    db: Session = Depends(get_db),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    code = _generate_unique_patient_code(db)
    patient = Patient(
        created_by=current_clinician.clinician_id,
        patient_code=code,
        full_name=encrypt_field(payload.full_name.strip()),
        age=payload.age,
        sex=payload.sex,
    )
    db.add(patient)
    db.flush()

    db.add(AuditLog(
        clinician_id=current_clinician.clinician_id,
        action="PATIENT_CREATED",
        entity_type="patients",
        entity_id=str(patient.patient_id),
    ))
    db.commit()
    db.refresh(patient)
    return _to_patient_response(patient)

@router.get("", response_model=List[PatientResponse])
def search_patients(
    query: Optional[str] = Query(None, description="Search by patient name or 6-char code"),
    db: Session = Depends(get_db),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    # Clinician sees only their own patients (Privacy by design)
    records = (
        db.query(Patient)
        .filter(Patient.created_by == current_clinician.clinician_id)
        .all()
    )

    results = [_to_patient_response(p) for p in records]
    if query and query.strip():
        q = query.strip().lower()
        results = [
            r for r in results
            if q in r.patient_code.lower() or q in r.full_name.lower()
        ]
    return results

@router.patch("/{patient_id}", response_model=PatientResponse)
def update_patient(
    patient_id: UUID,
    payload: PatientUpdateRequest,
    db: Session = Depends(get_db),
    current_clinician: Clinician = Depends(get_current_clinician),
):
    patient = (
        db.query(Patient)
        .filter(
            Patient.patient_id == patient_id,
            Patient.created_by == current_clinician.clinician_id,
        )
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    if payload.full_name is not None:
        patient.full_name = encrypt_field(payload.full_name.strip())
    if payload.age is not None:
        patient.age = payload.age
    if payload.sex is not None:
        patient.sex = payload.sex

    db.add(AuditLog(
        clinician_id=current_clinician.clinician_id,
        action="PATIENT_UPDATED",
        entity_type="patients",
        entity_id=str(patient.patient_id),
    ))
    db.commit()
    db.refresh(patient)
    return _to_patient_response(patient)
