from fastapi import APIRouter

from app.api.v1.analyses import router as analyses_router
from app.api.v1.auth import router as auth_router
from app.api.v1.explorations import router as explorations_router
from app.api.v1.interactions import router as interactions_router
from app.api.v1.knowledge_graph import router as knowledge_graph_router
from app.api.v1.health import router as health_router
from app.api.v1.projects import router as projects_router
from app.api.v1.reports import router as reports_router
from app.api.v1.search import router as search_router
from app.api.v1.system import router as system_router
from app.api.v1.test_runs import router as test_runs_router
from app.api.v1.workflows import router as workflows_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)
api_router.include_router(system_router)
api_router.include_router(projects_router)
api_router.include_router(reports_router)
api_router.include_router(search_router)
api_router.include_router(explorations_router)
api_router.include_router(interactions_router)
api_router.include_router(knowledge_graph_router)
api_router.include_router(analyses_router)
api_router.include_router(test_runs_router)
api_router.include_router(workflows_router)
