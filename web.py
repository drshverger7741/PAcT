import os
import sys
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import locale
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

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

def init_web(tracker_instance, db_module, stop_event_instance=None):
    global tracker, db, stop_event
    tracker = tracker_instance
    db = db_module
    stop_event = stop_event_instance

def format_hours(seconds: float) -> str:
    if seconds is None: return "0.00"
    return f"{seconds / 3600.0:.2f}"

def format_date_custom(date_str: str) -> str:
    """вт. 11 августа 2026"""
    try:
        dt = date.fromisoformat(date_str)
        # Названия месяцев в родительном падеже для русского
        months = ["января", "февраля", "марта", "апреля", "мая", "июня", 
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        weekdays = ["пн.", "вт.", "ср.", "чт.", "пт.", "сб.", "вс."]
        return f"{weekdays[dt.weekday()]} {dt.day} {months[dt.month-1]} {dt.year}"
    except Exception:
        return date_str

def get_month_name(month_idx: int) -> str:
    months = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", 
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    return months[month_idx-1]

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
                        weeks.append({"number": cw_num, "days": current_week, "total": week_total})
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
                    weeks.append({"number": cw_num, "days": current_week, "total": week_total})

            result.append({
                "year": year,
                "month": month,
                "is_current": (year == current_date.year and month == current_date.month),
                "total": month_total,
                "weeks": weeks
            })
    return result

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
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
            "visible_columns": visible_columns
        }
    )

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    idle_threshold = await db.get_setting("idle_threshold", "60")
    check_interval = await db.get_setting("check_interval", "5")
    flush_interval = await db.get_setting("flush_interval", "30")
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds")).split(",")
    
    all_columns = [
        ("active_seconds", "Активен"),
        ("idle_seconds", "Простой"),
        ("locked_seconds", "Заблокирован"),
        ("no_session_seconds", "Нет сеанса"),
        ("sleep_seconds", "Сон"),
        ("shutdown_seconds", "Выключен"),
        ("unknown_seconds", "Неизвестно")
    ]
    
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "idle_threshold": idle_threshold,
            "check_interval": check_interval,
            "flush_interval": flush_interval,
            "visible_columns": visible_columns,
            "all_columns": all_columns
        }
    )

@app.post("/settings")
async def save_settings(
    request: Request,
    idle_threshold: str = Form(...),
    check_interval: str = Form(...),
    flush_interval: str = Form(...),
    visible_columns: List[str] = Form([])
):
    await db.set_setting("idle_threshold", idle_threshold)
    await db.set_setting("check_interval", check_interval)
    await db.set_setting("flush_interval", flush_interval)
    await db.set_setting("visible_columns", ",".join(visible_columns))
    
    if tracker:
        try:
            tracker.idle_threshold = float(idle_threshold)
            tracker.check_interval = float(check_interval)
            tracker.flush_interval = float(flush_interval)
        except ValueError:
            pass
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/settings/reset")
async def reset_settings():
    default_idle = "300"
    default_check = "10"
    default_flush = "60"
    default_columns = "active_seconds,idle_seconds,locked_seconds,no_session_seconds,sleep_seconds,shutdown_seconds,unknown_seconds"
    
    await db.set_setting("idle_threshold", default_idle)
    await db.set_setting("check_interval", default_check)
    await db.set_setting("flush_interval", default_flush)
    await db.set_setting("visible_columns", default_columns)
    
    if tracker:
        tracker.idle_threshold = float(default_idle)
        tracker.check_interval = float(default_check)
        tracker.flush_interval = float(default_flush)
        
    return RedirectResponse(url="/settings", status_code=303)

@app.get("/api/day_details/{day_date}", response_class=HTMLResponse)
async def get_day_details(request: Request, day_date: str):
    log = await db.get_activity_log(day_date)
    # Переводим состояния на русский
    state_map = {
        "active": "Активен",
        "idle": "Простой",
        "locked": "Заблокирован",
        "no_session": "Нет сеанса",
        "sleep": "Сон",
        "unknown": "Неизвестно"
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
        context={"log": log, "day_date": day_date, "current_interval": current_interval}
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
    stats = await db.get_all_stats()
    grouped = await group_stats(stats)
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds")).split(",")
    return templates.TemplateResponse(
        request=request,
        name="stats_rows.html",
        context={"grouped_stats": grouped, "visible_columns": visible_columns}
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
