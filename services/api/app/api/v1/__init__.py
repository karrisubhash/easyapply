"""Version 1 of the API. The only place the version prefix is spelled."""

from fastapi import APIRouter

from app.api.v1 import jobs, matches, operations, profile, resume, tailoring

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(profile.router)
api_router.include_router(resume.router)
api_router.include_router(jobs.router)
api_router.include_router(matches.router)
api_router.include_router(tailoring.router)
# Internal. Behind its own authorization boundary, not the caller's identity.
api_router.include_router(operations.router)
