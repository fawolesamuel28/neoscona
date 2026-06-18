from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pathlib import Path

router = APIRouter(tags=["Neoscona Landing"])

# Path to Neoscona's static templates
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"


@router.get("/", response_class=HTMLResponse)
async def neoscona_landing():
    """Neoscona main landing page"""
    index_file = TEMPLATES_DIR / "neoscona.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text())
    return HTMLResponse(content="<h1>Neoscona</h1>", status_code=200)


@router.get("/blog", response_class=HTMLResponse)
async def neoscona_blog():
    """Neoscona blog page"""
    blog_file = TEMPLATES_DIR / "blog.html"
    if blog_file.exists():
        return HTMLResponse(content=blog_file.read_text())
    return HTMLResponse(content="<h1>Blog</h1>", status_code=200)


@router.get("/docs", response_class=HTMLResponse)
async def neoscona_docs():
    """Neoscona docs page"""
    docs_file = TEMPLATES_DIR / "docs.html"
    if docs_file.exists():
        return HTMLResponse(content=docs_file.read_text())
    return HTMLResponse(content="<h1>Docs</h1>", status_code=200)
