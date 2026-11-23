from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.routes_analysis import router as analysis_router
from app.api.routes_standards import router as standards_router
from app.api.dependencies import get_standard_registry
from app.normcontrol.guard import LocalRequestGuard


app = FastAPI(
    title="Document Processing API",
    description="API with UI for document and instruction processing",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

templates = Jinja2Templates(directory="templates")
app.add_middleware(LocalRequestGuard,reports_only=False)


@app.get("/", response_class=HTMLResponse)
async def get_ui(request: Request):
    standards = [item.model_dump() for item in get_standard_registry().list_standards()]
    return templates.TemplateResponse(request=request, name="index.html", context={"standards": standards})


app.include_router(analysis_router)
app.include_router(standards_router)

from app.normcontrol.api import api as normcontrol_api
app.mount("/normcontrol", normcontrol_api)
