from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import locale
from datetime import date, datetime
from typing import List
import utils

# Попытка установить русскую локаль для дат
try:
    locale.setlocale(locale.LC_TIME, 'russian')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
    except locale.Error:
        pass

app = FastAPI()
app.mount("/static", StaticFiles(directory=utils.get_static_path()), name="static")
templates = Jinja2Templates(directory=utils.get_templates_path())

# Глобальные переменные для доступа к данным трекера
tracker = None
db = None
stop_event = None

def init_web(tracker_instance, db_module, stop_event_instance=None):
    global tracker, db, stop_event
    tracker = tracker_instance
    db = db_module
    stop_event = stop_event_instance

templates.env.filters["hours"] = utils.format_hours
templates.env.filters["date_custom"] = utils.format_date_custom
templates.env.filters["month_name"] = utils.get_month_name

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    language = await db.get_setting("language", "ru")
    theme = await db.get_setting("theme", "dark")
    i18n = utils.get_i18n(language)
    custom_title = await db.get_setting("custom_title", "PAcT")
    stats = await db.get_all_stats()
    grouped = await utils.group_stats(stats, language)
    idle_threshold = await db.get_setting("idle_threshold", "60")
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds,locked_seconds,sleep_seconds")).split(",")
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "grouped_stats": grouped,
            "current_state": tracker.current_state if tracker else "unknown",
            "idle_threshold": idle_threshold,
            "visible_columns": visible_columns,
            "custom_title": custom_title,
            "language": language,
            "theme": theme,
            "i18n": i18n
        }
    )

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    language = await db.get_setting("language", "ru")
    theme = await db.get_setting("theme", "dark")
    i18n = utils.get_i18n(language)
    idle_threshold = await db.get_setting("idle_threshold", "60")
    check_interval = await db.get_setting("check_interval", "5")
    flush_interval = await db.get_setting("flush_interval", "30")
    custom_title = await db.get_setting("custom_title", "PAcT")
    track_window_activity = (await db.get_setting("track_window_activity", "true")).lower() == "true"
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds,locked_seconds,sleep_seconds")).split(",")
    
    all_columns = [
        ("active_seconds", i18n["active"]),
        ("idle_seconds", i18n["idle"]),
        ("locked_seconds", i18n["locked"]),
        ("sleep_seconds", i18n["sleep"])
    ]
    
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "idle_threshold": idle_threshold,
            "check_interval": check_interval,
            "flush_interval": flush_interval,
            "track_window_activity": track_window_activity,
            "custom_title": custom_title,
            "visible_columns": visible_columns,
            "all_columns": all_columns,
            "is_paused": tracker.paused if tracker else False,
            "language": language,
            "theme": theme,
            "i18n": i18n
        }
    )

@app.post("/settings")
async def save_settings(
    request: Request,
    idle_threshold: str = Form(...),
    check_interval: str = Form(...),
    flush_interval: str = Form(...),
    track_window_activity: bool = Form(False),
    custom_title: str = Form("PAcT"),
    language: str = Form("ru"),
    theme: str = Form("dark"),
    visible_columns: List[str] = Form([])
):
    await db.set_setting("idle_threshold", idle_threshold)
    await db.set_setting("check_interval", check_interval)
    await db.set_setting("flush_interval", flush_interval)
    await db.set_setting("track_window_activity", "true" if track_window_activity else "false")
    await db.set_setting("custom_title", custom_title)
    await db.set_setting("language", language)
    await db.set_setting("theme", theme)
    await db.set_setting("visible_columns", ",".join(visible_columns))
    
    if tracker:
        try:
            tracker.idle_threshold = float(idle_threshold)
            tracker.check_interval = float(check_interval)
            tracker.flush_interval = float(flush_interval)
            tracker.track_window_activity = track_window_activity
        except ValueError:
            pass
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/settings/reset")
async def reset_settings():
    default_idle = "300"
    default_check = "10"
    default_flush = "60"
    default_track = "true"
    default_lang = "ru"
    default_theme = "dark"
    default_title = "PAcT"
    default_columns = "active_seconds,idle_seconds,locked_seconds,sleep_seconds"
    
    await db.set_setting("idle_threshold", default_idle)
    await db.set_setting("check_interval", default_check)
    await db.set_setting("flush_interval", default_flush)
    await db.set_setting("track_window_activity", default_track)
    await db.set_setting("custom_title", default_title)
    await db.set_setting("language", default_lang)
    await db.set_setting("theme", default_theme)
    await db.set_setting("visible_columns", default_columns)
    
    if tracker:
        tracker.idle_threshold = float(default_idle)
        tracker.check_interval = float(default_check)
        tracker.flush_interval = float(default_flush)
        tracker.track_window_activity = True
        
    return RedirectResponse(url="/settings", status_code=303)

@app.get("/api/day_details/{day_date}", response_class=HTMLResponse)
async def get_day_details(request: Request, day_date: str):
    language = await db.get_setting("language", "ru")
    theme = await db.get_setting("theme", "dark")
    i18n = utils.get_i18n(language)
    log = await db.get_activity_log(day_date)
    # Переводим состояния
    state_map = {
        "active": i18n["active"],
        "idle": i18n["idle"],
        "locked": i18n["locked"],
        "no_session": i18n["no_session"],
        "sleep": i18n["sleep"],
        "unknown": i18n["unknown"]
    }
    for item in log:
        item['state_ru'] = state_map.get(item['state'], item['state'])
        item['state_class'] = f"state-{item['state']}"
    
    current_interval = None
    if tracker and day_date == date.today().isoformat():
        current_interval = {
            "start_time": datetime.fromtimestamp(tracker.last_state_change_time).strftime("%H:%M:%S"),
            "state": tracker.current_state,
            "state_ru": state_map.get(tracker.current_state, tracker.current_state)
        }
    
    return templates.TemplateResponse(
        request=request,
        name="day_details.html",
        context={
            "log": log,
            "day_date": day_date,
            "current_interval": current_interval,
            "language": language,
            "theme": theme,
            "i18n": i18n
        }
    )

@app.post("/api/day_comment/{day_date}")
async def update_day_comment_endpoint(day_date: str, comment: str = Form("")):
    comment = comment.strip()
    import logging
    logging.info(f"Updating day comment for {day_date}: '{comment}'")
    await db.update_day_comment(day_date, comment)
    return HTMLResponse(comment)

@app.post("/api/interval_comment/{interval_id}")
async def update_interval_comment_endpoint(interval_id: int, comment: str = Form("")):
    comment = comment.strip()
    import logging
    logging.info(f"Updating interval comment for {interval_id}: '{comment}'")
    await db.update_interval_comment(interval_id, comment)
    return HTMLResponse(comment)

@app.get("/api/stats", response_class=HTMLResponse)
async def get_stats(request: Request):
    language = await db.get_setting("language", "ru")
    i18n = utils.get_i18n(language)
    stats = await db.get_all_stats()
    grouped = await utils.group_stats(stats, language)
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds,locked_seconds,sleep_seconds")).split(",")
    return templates.TemplateResponse(
        request=request,
        name="stats_rows.html",
        context={
            "grouped_stats": grouped,
            "visible_columns": visible_columns,
            "language": language,
            "i18n": i18n
        }
    )

@app.get("/api/state", response_class=HTMLResponse)
async def get_state(request: Request):
    language = await db.get_setting("language", "ru")
    i18n = utils.get_i18n(language)
    state = tracker.current_state if tracker else "unknown"
    color = "green"
    if state == "idle": color = "yellow"
    elif state in ["locked", "no_session", "sleep"]: color = "red"
    
    state_map = {
        "active": i18n["active"],
        "idle": i18n["idle"],
        "locked": i18n["locked"],
        "no_session": i18n["no_session"],
        "sleep": i18n["sleep"],
        "unknown": i18n["unknown"]
    }
    text = state_map.get(state, state)
    
    if tracker and tracker.paused:
        text = f"{text} ({i18n.get('paused', 'Paused')})"
        color = "gray"
    
    return HTMLResponse(f'<span style="display:inline-block; width:12px; height:12px; border-radius:50%; background-color:{color}; margin-right:8px;"></span>{text}')

@app.get("/api/window_stats")
async def get_window_stats_endpoint(start: str, end: str):
    language = await db.get_setting("language", "ru")
    i18n = utils.get_i18n(language)
    stats = await db.get_window_stats(start, end)
    timeline = await db.get_window_timeline(start, end)
    
    # Агрегация по приложениям для диаграммы
    app_stats = {}
    for s in stats:
        app = s['app_name']
        app_stats[app] = app_stats.get(app, 0) + s['total_duration']
    
    # Сортировка по убыванию
    sorted_apps = sorted(app_stats.items(), key=lambda x: x[1], reverse=True)
    
    # Форматируем для ответа
    chart_data = {
        "labels": [x[0] for x in sorted_apps[:10]], # Топ 10 приложений
        "values": [round(x[1] / 3600.0, 2) for x in sorted_apps[:10]]
    }
    
    return JSONResponse({
        "chart": chart_data,
        "table": stats[:20], # Топ 20 окон/приложений
        "timeline": timeline
    })

@app.post("/api/shutdown")
async def shutdown():
    if stop_event:
        stop_event.set()
    return {"status": "shutting down"}

@app.post("/api/pause")
async def pause_monitoring():
    if tracker:
        tracker.pause()
    return {"status": "paused"}

@app.post("/api/resume")
async def resume_monitoring():
    if tracker:
        tracker.resume()
    return {"status": "resumed"}
