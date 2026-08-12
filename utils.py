import os
import sys
from datetime import date
from typing import List, Dict
from translations import TRANSLATIONS

def get_base_path():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def get_templates_path():
    return os.path.join(get_base_path(), "templates")

def get_static_path():
    return os.path.join(get_base_path(), "static")

def get_i18n(lang="ru"):
    return TRANSLATIONS.get(lang, TRANSLATIONS["ru"])

def format_hours(seconds: float) -> str:
    if seconds is None: return "0.00"
    return f"{seconds / 3600.0:.2f}"

def format_date_custom(date_str: str, lang="ru") -> str:
    """вт. 11 августа 2026"""
    try:
        dt = date.fromisoformat(date_str)
        i18n = TRANSLATIONS.get(lang, TRANSLATIONS["ru"])
        months = i18n["months"]
        weekdays = i18n["weekdays"]
        return f"{weekdays[dt.weekday()]} {dt.day} {months[dt.month-1]} {dt.year}"
    except Exception:
        return date_str

def get_month_name(month_idx: int, lang="ru") -> str:
    i18n = TRANSLATIONS.get(lang, TRANSLATIONS["ru"])
    return i18n["months_full"][month_idx-1]

async def group_stats(stats: List[Dict], lang="ru"):
    import db
    # Группировка по году и месяцу
    grouped = {}
    month_comments = await db.get_month_comments()
    week_comments = await db.get_week_comments()
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
                            "end_date": current_week[0]['date'],
                            "year_week": f"{s_dt.year}-{cw_num}",
                            "comment": week_comments.get(f"{s_dt.year}-{cw_num}", "")
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
                        "end_date": current_week[0]['date'],
                        "year_week": f"{first_dt.year}-{cw_num}",
                        "comment": week_comments.get(f"{first_dt.year}-{cw_num}", "")
                    })

            result.append({
                "year": year,
                "month": month,
                "is_current": (year == current_date.year and month == current_date.month),
                "total": month_total,
                "weeks": weeks,
                "start_date": month_start,
                "end_date": month_end,
                "year_month": f"{year}-{month:02d}",
                "comment": month_comments.get(f"{year}-{month:02d}", "")
            })
    return result
