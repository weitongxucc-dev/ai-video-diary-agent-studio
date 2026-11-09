"""Content template catalog HTTP; template declarations retain their catalog owner."""
from fastapi import APIRouter
from app.schemas import ContentTemplatesResponse

def build_content_template_router(*, response_for, catalog_for):
    router = APIRouter()

    @router.get('/api/content-templates', response_model=ContentTemplatesResponse)
    def get_content_templates() -> ContentTemplatesResponse:
        return response_for().model_validate({'templates': catalog_for()()}, from_attributes=True)
    return router
