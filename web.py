from fastapi import FastAPI, Request, Form, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import locale
import time
import os
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

def date_formatted_filter(date_str, lang="ru", date_format=None):
    if date_format is None:
        # Пытаемся получить из БД, если не передано (хотя фильтры обычно не асинхронны)
        # Но так как фильтр используется в шаблонах, лучше передавать его явно или иметь дефолт
        date_format = "dd.MM.yyyy"
    return utils.format_date_custom(date_str, lang, date_format)

templates.env.filters["date_formatted"] = date_formatted_filter

async def is_authenticated(request: Request):
    """Проверка, авторизован ли пользователь."""
    protection_enabled = (await db.get_setting("password_protection_enabled", "false")).lower() == "true"
    stored_hash = await db.get_setting("app_password_hash")
    
    # Если пароль не задан, защита не может быть активна (защита от lockout)
    if not protection_enabled or not stored_hash:
        return True
    
    auth_token = request.cookies.get("auth_token")
    if not auth_token:
        return False
    
    return auth_token == stored_hash

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    language = await db.get_setting("language", "ru")
    theme = await db.get_setting("theme", "dark")
    i18n = utils.get_i18n(language)
    custom_title = await db.get_setting("custom_title", "PAcT")
    password_protection_enabled = (await db.get_setting("password_protection_enabled", "false")).lower() == "true"
    
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "custom_title": custom_title,
            "language": language,
            "theme": theme,
            "i18n": i18n,
            "password_protection_enabled": password_protection_enabled
        }
    )

@app.post("/login")
async def login(password: str = Form(...)):
    # Если пароль в БД вообще не задан, позволяем войти и сбросить настройку (защита от lockout)
    stored_hash = await db.get_setting("app_password_hash")
    if not stored_hash:
        response = RedirectResponse(url="/?login=1", status_code=303)
        return response

    if await db.verify_password(password):
        # Добавляем параметр login=1 для JS, чтобы установить флаг в sessionStorage
        response = RedirectResponse(url="/?login=1", status_code=303)
        response.set_cookie(key="auth_token", value=stored_hash, httponly=True)
        return response
    
    # В случае ошибки перенаправляем обратно с параметром ошибки
    return RedirectResponse(url="/login?error=1", status_code=303)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("auth_token")
    return response

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not await is_authenticated(request):
        return RedirectResponse(url="/login")
    
    language = await db.get_setting("language", "ru")
    theme = await db.get_setting("theme", "dark")
    i18n = utils.get_i18n(language)
    custom_title = await db.get_setting("custom_title", "PAcT")
    date_format = await db.get_setting("date_format", "dd.MM.yyyy")
    stats = await db.get_all_stats()
    grouped = await utils.group_stats(stats, language)

    # Применяем формат даты ко всем уровням
    for month in grouped:
        for week in month['weeks']:
            for day in week['days']:
                day['formatted_date'] = utils.format_date_custom(day['date'], language, date_format)

    idle_threshold = await db.get_setting("idle_threshold", "60")
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds,locked_seconds,sleep_seconds")).split(",")
    password_protection_enabled = (await db.get_setting("password_protection_enabled", "false")).lower() == "true"
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
            "i18n": i18n,
            "password_protection_enabled": password_protection_enabled,
            "date_format": date_format
        }
    )
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not await is_authenticated(request):
        return RedirectResponse(url="/login")
    
    language = await db.get_setting("language", "ru")
    theme = await db.get_setting("theme", "dark")
    i18n = utils.get_i18n(language)
    idle_threshold = await db.get_setting("idle_threshold", "300")
    activity_grace_period = await db.get_setting("activity_grace_period", "5")
    check_interval = await db.get_setting("check_interval", "10")
    flush_interval = await db.get_setting("flush_interval", "30")
    custom_title = await db.get_setting("custom_title", "PAcT")
    track_window_activity = (await db.get_setting("track_window_activity", "true")).lower() == "true"
    password_protection_enabled = (await db.get_setting("password_protection_enabled", "false")).lower() == "true"
    auto_backup_enabled = (await db.get_setting("auto_backup_enabled", "false")).lower() == "true"
    date_format = await db.get_setting("date_format", "dd.MM.yyyy")
    has_password = (await db.get_setting("app_password_hash")) is not None
    visible_columns = (await db.get_setting("visible_columns", "active_seconds,idle_seconds,locked_seconds,sleep_seconds")).split(",")
    backups = []
    for b in await db.list_backups():
        b['date_display'] = utils.format_date_custom(b['date'].split(' ')[0], language, date_format) + " " + b['date'].split(' ')[1]
        backups.append(b)
    
    all_columns = [
        ("active_seconds", i18n["active"]),
        ("idle_seconds", i18n["idle"]),
        ("locked_seconds", i18n["locked"]),
        ("no_session_seconds", i18n["no_session"]),
        ("sleep_seconds", i18n["sleep"]),
        ("shutdown_seconds", i18n["shutdown"]),
        ("unknown_seconds", i18n["unknown"]),
    ]
    
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "idle_threshold": idle_threshold,
            "activity_grace_period": activity_grace_period,
            "check_interval": check_interval,
            "flush_interval": flush_interval,
            "track_window_activity": track_window_activity,
            "password_protection_enabled": password_protection_enabled,
            "auto_backup_enabled": auto_backup_enabled,
            "has_password": has_password,
            "custom_title": custom_title,
            "visible_columns": visible_columns,
            "all_columns": all_columns,
            "backups": backups,
            "is_paused": tracker.paused if tracker else False,
            "language": language,
            "theme": theme,
            "i18n": i18n,
            "password_protection_enabled": password_protection_enabled,
            "date_format": date_format
        }
    )

@app.post("/settings")
async def save_settings(
    request: Request,
    idle_threshold: str = Form(...),
    activity_grace_period: str = Form(...),
    check_interval: str = Form(...),
    flush_interval: str = Form(...),
    track_window_activity: bool = Form(False),
    password_protection_enabled: bool = Form(False),
    auto_backup_enabled: bool = Form(False),
    custom_title: str = Form("PAcT"),
    language: str = Form("ru"),
    theme: str = Form("dark"),
    date_format: str = Form("dd.MM.yyyy"),
    visible_columns: List[str] = Form([])
):
    if not await is_authenticated(request):
        return RedirectResponse(url="/login")
    
    # Server-side validation
    errors = {}
    i18n = utils.get_i18n(language)
    
    # Check if language is valid
    if language not in ["ru", "en"]:
        language = "ru" # Default back to ru if invalid
        i18n = utils.get_i18n(language)
    
    # Check if theme is valid
    if theme not in ["light", "dark"]:
        theme = "dark"

    try:
        val = int(idle_threshold)
        if val < 5:
            errors["idle_threshold"] = i18n["error_min_value"].format(min=5)
    except ValueError:
        errors["idle_threshold"] = i18n["error_invalid_number"]
        
    try:
        val = int(activity_grace_period)
        if val < 0:
            errors["activity_grace_period"] = i18n["error_min_value"].format(min=0)
    except ValueError:
        errors["activity_grace_period"] = i18n["error_invalid_number"]
        
    try:
        val = int(check_interval)
        if val < 1:
            errors["check_interval"] = i18n["error_min_value"].format(min=1)
    except ValueError:
        errors["check_interval"] = i18n["error_invalid_number"]
        
    try:
        val = int(flush_interval)
        if val < 5:
            errors["flush_interval"] = i18n["error_min_value"].format(min=5)
    except ValueError:
        errors["flush_interval"] = i18n["error_invalid_number"]
        
    if len(custom_title) > 50:
        errors["custom_title"] = i18n["error_max_length"].format(max=50)

    if errors:
        all_columns = [
            ("active_seconds", i18n["act_short"]),
            ("idle_seconds", i18n["idle_short"]),
            ("locked_seconds", i18n["locked_short"]),
            ("no_session_seconds", i18n["no_session_short"]),
            ("sleep_seconds", i18n["sleep_short"]),
            ("shutdown_seconds", i18n["shutdown_short"]),
            ("unknown_seconds", i18n["unknown_short"]),
        ]
        has_password = (await db.get_setting("app_password_hash")) is not None
        
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "idle_threshold": idle_threshold,
                "activity_grace_period": activity_grace_period,
                "check_interval": check_interval,
                "flush_interval": flush_interval,
                "track_window_activity": track_window_activity,
                "custom_title": custom_title,
                "visible_columns": visible_columns,
                "all_columns": all_columns,
                "is_paused": tracker.paused if tracker else False,
                "language": language,
                "theme": theme,
                "i18n": i18n,
                "password_protection_enabled": password_protection_enabled,
                "auto_backup_enabled": auto_backup_enabled,
                "date_format": date_format,
                "has_password": has_password,
                "errors": errors
            }
        )
    
    await db.set_setting("idle_threshold", idle_threshold)
    await db.set_setting("activity_grace_period", activity_grace_period)
    await db.set_setting("check_interval", check_interval)
    await db.set_setting("flush_interval", flush_interval)
    await db.set_setting("track_window_activity", "true" if track_window_activity else "false")
    await db.set_setting("password_protection_enabled", "true" if password_protection_enabled else "false")
    await db.set_setting("auto_backup_enabled", "true" if auto_backup_enabled else "false")
    await db.set_setting("custom_title", custom_title)
    await db.set_setting("language", language)
    await db.set_setting("theme", theme)
    await db.set_setting("date_format", date_format)
    await db.set_setting("visible_columns", ",".join(visible_columns))
    
    if tracker:
        try:
            tracker.idle_threshold = float(idle_threshold)
            tracker.activity_grace_period = float(activity_grace_period)
            tracker.check_interval = float(check_interval)
            tracker.flush_interval = float(flush_interval)
            tracker.track_window_activity = track_window_activity
        except ValueError:
            pass

    # Принудительно отключаем защиту, если пароль не задан
    if password_protection_enabled:
        has_password = (await db.get_setting("app_password_hash")) is not None
        if not has_password:
            await db.set_setting("password_protection_enabled", "false")
            
    return RedirectResponse(url="/settings", status_code=303)

@app.post("/api/settings/password")
async def change_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    if new_password != confirm_password:
        return JSONResponse({"status": "error", "message": "passwords_dont_match"}, status_code=400)
    
    password_hash = db.hash_password(new_password)
    await db.set_setting("app_password_hash", password_hash)
    
    response = JSONResponse({"status": "success", "message": "password_saved"})
    response.set_cookie(key="auth_token", value=password_hash, httponly=True)
    return response

@app.post("/api/settings/reset")
async def reset_settings(request: Request):
    if not await is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    
    default_idle = "300"
    default_grace = "5"
    default_check = "10"
    default_flush = "60"
    default_track = "true"
    default_lang = "ru"
    default_theme = "dark"
    default_title = "PAcT"
    default_date_format = "dd.MM.yyyy"
    default_columns = "active_seconds,idle_seconds,locked_seconds,sleep_seconds"
    default_password_protection = "false"
    default_auto_backup = "false"
    
    await db.set_setting("idle_threshold", default_idle)
    await db.set_setting("activity_grace_period", default_grace)
    await db.set_setting("check_interval", default_check)
    await db.set_setting("flush_interval", default_flush)
    await db.set_setting("track_window_activity", default_track)
    await db.set_setting("password_protection_enabled", default_password_protection)
    await db.set_setting("auto_backup_enabled", default_auto_backup)
    await db.set_setting("custom_title", default_title)
    await db.set_setting("language", default_lang)
    await db.set_setting("theme", default_theme)
    await db.set_setting("date_format", default_date_format)
    await db.set_setting("visible_columns", default_columns)
    
    if tracker:
        tracker.idle_threshold = float(default_idle)
        tracker.activity_grace_period = float(default_grace)
        tracker.check_interval = float(default_check)
        tracker.flush_interval = float(default_flush)
        tracker.track_window_activity = True
        
    return RedirectResponse(url="/settings", status_code=303)

@app.get("/api/day_details/{day_date}", response_class=HTMLResponse)
async def get_day_details(request: Request, day_date: str):
    if not await is_authenticated(request):
        return HTMLResponse(content="Unauthorized", status_code=401)
    
    language = await db.get_setting("language", "ru")
    theme = await db.get_setting("theme", "dark")
    date_format = await db.get_setting("date_format", "dd.MM.yyyy")
    i18n = utils.get_i18n(language)
    log = await db.get_activity_log(day_date)
    # Переводим состояния
    state_map = {
        "active": i18n["active"],
        "idle": i18n["idle"],
        "locked": i18n["locked"],
        "no_session": i18n["no_session"],
        "sleep": i18n["sleep"],
        "startup": i18n.get("startup", "Startup"),
        "shutdown": i18n["shutdown"],
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
    
    password_protection_enabled = (await db.get_setting("password_protection_enabled", "false")).lower() == "true"
    
    return templates.TemplateResponse(
        request=request,
        name="day_details.html",
        context={
            "log": log,
            "day_date": day_date,
            "formatted_date": utils.format_date_custom(day_date, language, date_format),
            "current_interval": current_interval,
            "language": language,
            "theme": theme,
            "i18n": i18n,
            "password_protection_enabled": password_protection_enabled
        }
    )

@app.post("/api/day_comment/{day_date}")
async def update_day_comment_endpoint(request: Request, day_date: str, comment: str = Form("")):
    if not await is_authenticated(request):
        return HTMLResponse(content="Unauthorized", status_code=401)
    
    comment = comment.strip()
    import logging
    logging.info(f"Updating day comment for {day_date}: '{comment}'")
    await db.update_day_comment(day_date, comment)
    return HTMLResponse(comment)

@app.post("/api/interval_comment/{interval_id}")
async def update_interval_comment_endpoint(request: Request, interval_id: int, comment: str = Form("")):
    if not await is_authenticated(request):
        return HTMLResponse(content="Unauthorized", status_code=401)
    
    comment = comment.strip()
    import logging
    logging.info(f"Updating interval comment for {interval_id}: '{comment}'")
    await db.update_interval_comment(interval_id, comment)
    return HTMLResponse(comment)

@app.post("/api/month_comment/{year_month}")
async def update_month_comment_endpoint(request: Request, year_month: str, comment: str = Form("")):
    if not await is_authenticated(request):
        return HTMLResponse(content="Unauthorized", status_code=401)
    
    comment = comment.strip()
    await db.update_month_comment(year_month, comment)
    return HTMLResponse(comment)

@app.post("/api/week_comment/{year_week}")
async def update_week_comment_endpoint(request: Request, year_week: str, comment: str = Form("")):
    if not await is_authenticated(request):
        return HTMLResponse(content="Unauthorized", status_code=401)
    
    comment = comment.strip()
    await db.update_week_comment(year_week, comment)
    return HTMLResponse(comment)

@app.get("/api/stats", response_class=HTMLResponse)
async def get_stats(request: Request):
    if not await is_authenticated(request):
        return HTMLResponse(content="Unauthorized", status_code=401)
    
    language = await db.get_setting("language", "ru")
    date_format = await db.get_setting("date_format", "dd.MM.yyyy")
    i18n = utils.get_i18n(language)
    stats = await db.get_all_stats()
    grouped = await utils.group_stats(stats, language)

    # Применяем формат даты ко всем уровням
    for month in grouped:
        for week in month['weeks']:
            for day in week['days']:
                day['formatted_date'] = utils.format_date_custom(day['date'], language, date_format)

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
    if not await is_authenticated(request):
        return HTMLResponse(content="Unauthorized", status_code=401)
    
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
        "startup": i18n.get("startup", "Startup"),
        "shutdown": i18n["shutdown"],
        "unknown": i18n["unknown"]
    }
    text = state_map.get(state, state)
    
    if state == "idle" and tracker:
        idle_duration = int(time.time() - tracker.last_state_change_time)
        h = idle_duration // 3600
        m = (idle_duration % 3600) // 60
        s = idle_duration % 60
        
        # Локализация формата времени простоя
        if h > 0:
            time_str = f"{h:02}:{m:02}:{s:02}"
        else:
            time_str = f"{m:02}:{s:02}"
        text = f"{text} ({time_str})"
    
    if tracker and tracker.paused:
        text = f"{text} ({i18n.get('paused', 'Paused')})"
        color = "gray"
    
    return HTMLResponse(f'<span style="display:inline-block; width:12px; height:12px; border-radius:50%; background-color:{color}; margin-right:8px;"></span>{text}')

@app.get("/api/window_stats")
async def get_window_stats_endpoint(request: Request, start: str, end: str):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
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
    date_format = await db.get_setting("date_format", "dd.MM.yyyy")
    for item in timeline:
        item['date'] = utils.format_date_custom(item['date'], language, date_format)

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
async def shutdown(request: Request):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    if stop_event:
        stop_event.set()
    return {"status": "shutting down"}

@app.post("/api/pause")
async def pause_monitoring(request: Request):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    if tracker:
        tracker.pause()
    return {"status": "paused"}

@app.post("/api/resume")
async def resume_monitoring(request: Request):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    if tracker:
        tracker.resume()
    return {"status": "resumed"}

@app.post("/api/backups/create")
async def create_backup_endpoint(request: Request):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    filename = await db.create_backup()
    if filename:
        return RedirectResponse(url="/settings", status_code=303)
    return JSONResponse({"status": "error", "message": "Failed to create backup"}, status_code=500)

@app.get("/api/backups/download/{filename}")
async def download_backup(request: Request, filename: str):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    path = os.path.join(db.get_db_path(), "backups", filename)
    if os.path.exists(path):
        return FileResponse(path, filename=filename)
    return JSONResponse({"status": "error", "message": "File not found"}, status_code=404)

@app.post("/api/backups/restore/{filename}")
async def restore_backup_endpoint(request: Request, filename: str):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    success = await db.restore_backup(filename)
    if success:
        # После восстановления БД лучше перезагрузить приложение или хотя бы уведомить пользователя, 
        # что данные могут не обновиться в памяти мгновенно.
        # Но для простоты просто перенаправляем.
        return RedirectResponse(url="/settings?restored=1", status_code=303)
    return JSONResponse({"status": "error", "message": "Failed to restore backup"}, status_code=500)

@app.post("/api/backups/delete/{filename}")
async def delete_backup_endpoint(request: Request, filename: str):
    if not await is_authenticated(request):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    path = os.path.join(db.get_db_path(), "backups", filename)
    if os.path.exists(path):
        os.remove(path)
        return RedirectResponse(url="/settings", status_code=303)
    return JSONResponse({"status": "error", "message": "File not found"}, status_code=404)
