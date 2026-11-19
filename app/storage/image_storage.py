import os
import base64
from abc import ABC, abstractmethod
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.core.config import settings

_AES_KEY = base64.b64decode(settings.AES_ENCRYPTION_KEY)
_aesgcm = AESGCM(_AES_KEY)

class ImageStorage(ABC):
    @abstractmethod
    def save_encrypted(self, relative_key: str, raw_bytes: bytes) -> str:
        pass

    @abstractmethod
    def load_decrypted(self, relative_key: str) -> bytes:
        pass

    @abstractmethod
    def delete_file(self, relative_key: str) -> None:
        pass

class LocalEncryptedImageStorage(ImageStorage):
    """Stores AES-256-GCM encrypted images in local folder (Part 1)."""
    def __init__(self, base_dir: str = settings.LOCAL_STORAGE_PATH):
        self.base_path = Path(base_dir)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_encrypted(self, relative_key: str, raw_bytes: bytes) -> str:
        full_path = self.base_path / relative_key
        full_path.parent.mkdir(parents=True, exist_ok=True)

        nonce = os.urandom(12)
        ciphertext = _aesgcm.encrypt(nonce, raw_bytes, None)
        full_path.write_bytes(nonce + ciphertext)
        return relative_key

    def load_decrypted(self, relative_key: str) -> bytes:
        full_path = self.base_path / relative_key
        data = full_path.read_bytes()
        nonce, ciphertext = data[:12], data[12:]
        return _aesgcm.decrypt(nonce, ciphertext, None)

    def delete_file(self, relative_key: str) -> None:
        full_path = self.base_path / relative_key
        if full_path.exists():
            full_path.unlink()

def get_image_storage() -> ImageStorage:
    return LocalEncryptedImageStorage()
