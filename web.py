import os
import sys
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from typing import Optional

def get_base_path():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def get_templates_path():
    return os.path.join(get_base_path(), "templates")

def get_static_path():
    return os.path.join(get_base_path(), "static")

app = FastAPI()
app.mount("/static", StaticFiles(directory=get_static_path()), name="static")
templates = Jinja2Templates(directory=get_templates_path())

# Глобальные переменные для доступа к данным трекера
tracker = None
db = None
stop_event = None

def init_web(tracker_instance, db_module, stop_event_instance=None):
    global tracker, db, stop_event
    tracker = tracker_instance
    db = db_module
    stop_event = stop_event_instance

def format_hours(seconds: float) -> str:
    return f"{seconds / 3600.0:.2f}"

templates.env.filters["hours"] = format_hours

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = await db.get_all_stats()
    idle_threshold = await db.get_setting("idle_threshold", "60")
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "stats": stats,
            "current_state": tracker.current_state if tracker else "unknown",
            "idle_threshold": idle_threshold
        }
    )

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    idle_threshold = await db.get_setting("idle_threshold", "60")
    check_interval = await db.get_setting("check_interval", "5")
    flush_interval = await db.get_setting("flush_interval", "30")
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "idle_threshold": idle_threshold,
            "check_interval": check_interval,
            "flush_interval": flush_interval
        }
    )

@app.post("/settings")
async def save_settings(
    idle_threshold: str = Form(...),
    check_interval: str = Form(...),
    flush_interval: str = Form(...)
):
    await db.set_setting("idle_threshold", idle_threshold)
    await db.set_setting("check_interval", check_interval)
    await db.set_setting("flush_interval", flush_interval)
    
    if tracker:
        try:
            tracker.idle_threshold = float(idle_threshold)
            tracker.check_interval = float(check_interval)
            tracker.flush_interval = float(flush_interval)
        except ValueError:
            pass
    return RedirectResponse(url="/", status_code=303)

@app.get("/api/stats", response_class=HTMLResponse)
async def get_stats(request: Request):
    stats = await db.get_all_stats()
    return templates.TemplateResponse(
        request=request,
        name="stats_rows.html",
        context={"stats": stats}
    )

@app.get("/api/state", response_class=HTMLResponse)
async def get_state(request: Request):
    state = tracker.current_state if tracker else "unknown"
    color = "green"
    if state == "idle": color = "yellow"
    elif state in ["locked", "no_session", "sleep"]: color = "red"
    
    text = {
        "active": "Активен",
        "idle": "Простой",
        "locked": "Заблокирован",
        "no_session": "Нет сеанса",
        "sleep": "Сон",
        "unknown": "Неизвестно"
    }.get(state, state)
    
    return HTMLResponse(f'<span style="display:inline-block; width:12px; height:12px; border-radius:50%; background-color:{color}; margin-right:8px;"></span>{text}')

@app.post("/api/shutdown")
async def shutdown():
    if stop_event:
        stop_event.set()
    return {"status": "shutting down"}
