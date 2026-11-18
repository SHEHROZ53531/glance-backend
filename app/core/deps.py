from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.session import get_db
from app.db.models import Clinician, AccountStatusEnum

bearer_scheme = HTTPBearer()

def get_current_clinician(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Clinician:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        clinician_id_str: str = payload.get("sub")
        if clinician_id_str is None or payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired, please log in again.",
            )
        clinician_id = UUID(clinician_id_str)
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired, please log in again.",
        )

    clinician = db.query(Clinician).filter(Clinician.clinician_id == clinician_id).first()
    if not clinician or clinician.status != AccountStatusEnum.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account inactive or not found.",
        )
    return clinician
