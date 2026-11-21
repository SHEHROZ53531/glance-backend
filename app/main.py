from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.auth import router as auth_router
from app.api.v1.patients import router as patients_router
from app.api.v1.assessments import router as assessments_router
from app.api.v1.screening import router as screening_router

app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(patients_router)
app.include_router(assessments_router)
app.include_router(screening_router)

@app.get("/v1/health")
def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENV,
        "service": "GLANCE Backend API"
    }
