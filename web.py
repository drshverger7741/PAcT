import os
import sys
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import locale
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from translations import TRANSLATIONS

# Попытка установить русскую локаль для дат
try:
    locale.setlocale(locale.LC_TIME, 'russian')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
    except locale.Error:
        pass

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
current_lang = "ru"

def init_web(tracker_instance, db_module, stop_event_instance=None):
    global tracker, db, stop_event
    tracker = tracker_instance
    db = db_module
    stop_event = stop_event_instance

def get_i18n():
    return TRANSLATIONS.get(current_lang, TRANSLATIONS["ru"])

def format_hours(seconds: float) -> str:
    if seconds is None: return "0.00"
    return f"{seconds / 3600.0:.2f}"

def format_date_custom(date_str: str) -> str:
    """вт. 11 августа 2026"""
    try:
        dt = date.fromisoformat(date_str)
        i18n = get_i18n()
        months = i18n["months"]
        weekdays = i18n["weekdays"]
        return f"{weekdays[dt.weekday()]} {dt.day} {months[dt.month-1]} {dt.year}"
    except Exception:
        return date_str

def get_month_name(month_idx: int) -> str:
    i18n = get_i18n()
    return i18n["months_full"][month_idx-1]

templates.env.filters["hours"] = format_hours
templates.env.filters["date_custom"] = format_date_custom
templates.env.filters["month_name"] = get_month_name

async def group_stats(stats: List[Dict]):
    # Группировка по году и месяцу
    grouped = {}
    for s in stats:
        dt = date.fromisoformat(s['date'])
        year = dt.year
        month = dt.month
        if year not in grouped: grouped[year] = {}
        if month not in grouped[year]: grouped[year][month] = []
        grouped[year][month].append(s)
    
    # Расчет сумм и добавление недель
    result = []
    current_date = date.today()
    for year in sorted(grouped.keys(), reverse=True):
        for month in sorted(grouped[year].keys(), reverse=True):
            month_stats = grouped[year][month]
            month_total = {
                "active_seconds": sum(x.get("active_seconds", 0) for x in month_stats),
                "idle_seconds": sum(x.get("idle_seconds", 0) for x in month_stats),
                "locked_seconds": sum(x.get("locked_seconds", 0) for x in month_stats),
                "no_session_seconds": sum(x.get("no_session_seconds", 0) for x in month_stats),
                "sleep_seconds": sum(x.get("sleep_seconds", 0) for x in month_stats),
                "shutdown_seconds": sum(x.get("shutdown_seconds", 0) for x in month_stats),
                "unknown_seconds": sum(x.get("unknown_seconds", 0) for x in month_stats)
            }
            
            # Разделение по неделям
            weeks = []
            month_stats_sorted = sorted(month_stats, key=lambda x: x['date'], reverse=True)
            
            # Для статистики месяца: даты начала и конца
            month_start = month_stats_sorted[-1]['date'] if month_stats_sorted else ""
            month_end = month_stats_sorted[0]['date'] if month_stats_sorted else ""
            
            current_week = []
            if month_stats_sorted:
                first_dt = date.fromisoformat(month_stats_sorted[0]['date'])
                # ISO week
                cw_num = first_dt.isocalendar()[1]
                
                for s in month_stats_sorted:
                    s_dt = date.fromisoformat(s['date'])
                    s_cw = s_dt.isocalendar()[1]
                    s['is_today'] = (s['date'] == current_date.isoformat())
                    if s_cw != cw_num:
                        # Завершаем неделю
                        week_total = {
                            "active_seconds": sum(x.get("active_seconds", 0) for x in current_week),
                            "idle_seconds": sum(x.get("idle_seconds", 0) for x in current_week),
                            "locked_seconds": sum(x.get("locked_seconds", 0) for x in current_week),
                            "no_session_seconds": sum(x.get("no_session_seconds", 0) for x in current_week),
                            "sleep_seconds": sum(x.get("sleep_seconds", 0) for x in current_week),
                            "shutdown_seconds": sum(x.get("shutdown_seconds", 0) for x in current_week),
                            "unknown_seconds": sum(x.get("unknown_seconds", 0) for x in current_week)
                        }
                        weeks.append({
                            "number": cw_num, 
                            "days": current_week, 
                            "total": week_total,
                            "start_date": current_week[-1]['date'],
                            "end_date": current_week[0]['date']
                        })
                        current_week = []
                        cw_num = s_cw
                    current_week.append(s)
                
                if current_week:
                    week_total = {
                        "active_seconds": sum(x.get("active_seconds", 0) for x in current_week),
                        "idle_seconds": sum(x.get("idle_seconds", 0) for x in current_week),
                        "locked_seconds": sum(x.get("locked_seconds", 0) for x in current_week),
                        "no_session_seconds": sum(x.get("no_session_seconds", 0) for x in current_week),
                        "sleep_seconds": sum(x.get("sleep_seconds", 0) for x in current_week),
                        "shutdown_seconds": sum(x.get("shutdown_seconds", 0) for x in current_week),
                        "unknown_seconds": sum(x.get("unknown_seconds", 0) for x in current_week)
                    }
                    weeks.append({
                        "number": cw_num, 
                        "days": current_week, 
                        "total": week_total,
                        "start_date": current_week[-1]['date'],
                        "end_date": current_week[0]['date']
                    })

            result.append({
                "year": year,
                "month": month,
                "is_current": (year == current_date.year and month == current_date.month),
                "total": month_total,
                "weeks": weeks,
                "start_date": month_start,
                "end_date": month_end
            })
    return result

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    global current_lang
    current_lang = await db.get_setting("language", "ru")
    i18n = get_i18n()
    custom_title = await db.get_setting("custom_title", "PAcT")
    stats = await db.get_all_stats()
    grouped = await group_stats(stats)
    idle_threshold = await db.get_setting("idle_threshold", "60")
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds")).split(",")
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "grouped_stats": grouped,
            "current_state": tracker.current_state if tracker else "unknown",
            "idle_threshold": idle_threshold,
            "visible_columns": visible_columns,
            "custom_title": custom_title,
            "i18n": i18n
        }
    )

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    global current_lang
    current_lang = await db.get_setting("language", "ru")
    i18n = get_i18n()
    idle_threshold = await db.get_setting("idle_threshold", "60")
    check_interval = await db.get_setting("check_interval", "5")
    flush_interval = await db.get_setting("flush_interval", "30")
    custom_title = await db.get_setting("custom_title", "PAcT")
    track_window_activity = (await db.get_setting("track_window_activity", "true")).lower() == "true"
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds")).split(",")
    
    all_columns = [
        ("active_seconds", i18n["active"]),
        ("idle_seconds", i18n["idle"]),
        ("locked_seconds", i18n["locked"]),
        ("no_session_seconds", i18n["no_session"]),
        ("sleep_seconds", i18n["sleep"]),
        ("shutdown_seconds", i18n["shutdown"]),
        ("unknown_seconds", i18n["unknown"])
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
    visible_columns: List[str] = Form([])
):
    await db.set_setting("idle_threshold", idle_threshold)
    await db.set_setting("check_interval", check_interval)
    await db.set_setting("flush_interval", flush_interval)
    await db.set_setting("track_window_activity", "true" if track_window_activity else "false")
    await db.set_setting("custom_title", custom_title)
    await db.set_setting("language", language)
    await db.set_setting("visible_columns", ",".join(visible_columns))
    
    global current_lang
    current_lang = language
    
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
    default_title = "PAcT"
    default_columns = "active_seconds,idle_seconds,locked_seconds,no_session_seconds,sleep_seconds,shutdown_seconds,unknown_seconds"
    
    await db.set_setting("idle_threshold", default_idle)
    await db.set_setting("check_interval", default_check)
    await db.set_setting("flush_interval", default_flush)
    await db.set_setting("track_window_activity", default_track)
    await db.set_setting("custom_title", default_title)
    await db.set_setting("language", default_lang)
    await db.set_setting("visible_columns", default_columns)
    
    global current_lang
    current_lang = default_lang
    
    if tracker:
        tracker.idle_threshold = float(default_idle)
        tracker.check_interval = float(default_check)
        tracker.flush_interval = float(default_flush)
        tracker.track_window_activity = True
        
    return RedirectResponse(url="/settings", status_code=303)

@app.get("/api/day_details/{day_date}", response_class=HTMLResponse)
async def get_day_details(request: Request, day_date: str):
    global current_lang
    current_lang = await db.get_setting("language", "ru")
    i18n = get_i18n()
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
    global current_lang
    current_lang = await db.get_setting("language", "ru")
    i18n = get_i18n()
    stats = await db.get_all_stats()
    grouped = await group_stats(stats)
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds")).split(",")
    return templates.TemplateResponse(
        request=request,
        name="stats_rows.html",
        context={
            "grouped_stats": grouped,
            "visible_columns": visible_columns,
            "i18n": i18n
        }
    )

@app.get("/api/state", response_class=HTMLResponse)
async def get_state(request: Request):
    global current_lang
    current_lang = await db.get_setting("language", "ru")
    i18n = get_i18n()
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
