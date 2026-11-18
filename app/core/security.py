import os
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from passlib.context import CryptContext
from jose import jwt
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Decode 32-byte (256-bit) AES key from .env
_AES_KEY = base64.b64decode(settings.AES_ENCRYPTION_KEY)
_aesgcm = AESGCM(_AES_KEY)

def encrypt_field(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt sensitive text using AES-256-GCM before DB save."""
    if plaintext is None:
        return None
    nonce = os.urandom(12)
    ciphertext = _aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")

def decrypt_field(encrypted_b64: Optional[str]) -> Optional[str]:
    """Decrypt AES-256-GCM field when requested by owning clinician."""
    if encrypted_b64 is None:
        return None
    raw = base64.b64decode(encrypted_b64.encode("utf-8"))
    nonce, ciphertext = raw[:12], raw[12:]
    return _aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def hash_token_or_code(value: str) -> str:
    """SHA-256 hash for 6-digit email codes and refresh tokens."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(subject), "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
