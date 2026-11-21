from abc import ABC, abstractmethod
from typing import Dict, Any
import struct
import zlib

def _generate_placeholder_png(width: int = 64, height: int = 64) -> bytes:
    """Generates a valid minimal translucent red/amber RGBA PNG for Grad-CAM overlay testing."""
    raw_rows = []
    for y in range(height):
        row = bytearray([0])  # filter type 0
        for x in range(width):
            dist = ((x - width / 2) ** 2 + (y - height / 2) ** 2) ** 0.5
            intensity = max(0, min(255, int(255 - dist * 6)))
            row.extend([255, max(40, 200 - intensity), 30, 140])  # R, G, B, Alpha
        raw_rows.append(bytes(row))
    raw_data = b"".join(raw_rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw_data)) + chunk(b"IEND", b"")

class InferenceClient(ABC):
    @abstractmethod
    def run_screening(self, surfaces: Dict[str, bytes]) -> Dict[str, Any]:
        pass

class MockGlanceInferenceClient(InferenceClient):
    """
    Placeholder GLANCE-Net client used while multi-surface models are completing training.
    Matches the exact output schema of the production multi-surface fusion transformer.
    """
    def run_screening(self, surfaces: Dict[str, bytes]) -> Dict[str, Any]:
        num_surfaces = len(surfaces)
        base_uncertainty = round(max(0.08, 0.32 - (num_surfaces * 0.04)), 3)

        predictions = [
            {"condition_code": "ANEMIA", "risk_score": 0.72, "confidence": 0.86},
            {"condition_code": "JAUNDICE", "risk_score": 0.18, "confidence": 0.91},
            {"condition_code": "CYANOSIS", "risk_score": 0.12, "confidence": 0.89},
            {"condition_code": "DEHYDRATION", "risk_score": 0.44, "confidence": 0.78},
            {"condition_code": "PALMAR_ERYTHEMA", "risk_score": 0.15, "confidence": 0.84},
        ]

        weight = round(1.0 / max(1, num_surfaces), 3)
        surface_outputs = {}
        for surface_name in surfaces.keys():
            surface_outputs[surface_name] = {
                "heatmap_png": _generate_placeholder_png(),
                "surface_contribution": weight,
                "surface_uncertainty": round(base_uncertainty + 0.02, 3),
            }

        return {
            "model_version": "glance-fusion-v0.1-mock",
            "risk_level": "Moderate",
            "uncertainty": base_uncertainty,
            "needs_referral": True,
            "predictions": predictions,
            "surfaces": surface_outputs,
        }

def get_inference_client() -> InferenceClient:
    return MockGlanceInferenceClient()
