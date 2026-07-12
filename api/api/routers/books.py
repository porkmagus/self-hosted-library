"""Books router — book listing and management."""

# This is handled in ingest.py for now, merged for simplicity.
# Re-export the ingest router as books for compatibility.
from fastapi import APIRouter

router = APIRouter()

# Book listing is in ingest_router already via /books endpoint
# This module is kept for potential future expansion
