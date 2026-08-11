import aiosqlite
import os
import sys
from datetime import date
from typing import List, Dict, Any

def get_db_path() -> str:
    """Возвращает путь к папке приложения."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(get_db_path(), "activity_monitor.db")
LOG_PATH = os.path.join(get_db_path(), "app.log")

async def init_db():
    """Инициализация таблиц в БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                active_seconds REAL DEFAULT 0,
                idle_seconds REAL DEFAULT 0,
                locked_seconds REAL DEFAULT 0,
                no_session_seconds REAL DEFAULT 0,
                sleep_seconds REAL DEFAULT 0,
                shutdown_seconds REAL DEFAULT 0,
                unknown_seconds REAL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Начальные настройки, если их нет
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('idle_threshold', '60')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('check_interval', '5')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('flush_interval', '30')")
        await db.commit()

async def get_setting(key: str, default: Any = None) -> Any:
    """Получение значения настройки."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: Any):
    """Сохранение значения настройки."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()

async def upsert_daily_stats(stats_date: str, data: Dict[str, float]):
    """Обновление или вставка статистики за день."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем наличие записи
        async with db.execute("SELECT 1 FROM daily_stats WHERE date = ?", (stats_date,)) as cursor:
            exists = await cursor.fetchone()

        if exists:
            update_query = """
                UPDATE daily_stats 
                SET active_seconds = active_seconds + ?,
                    idle_seconds = idle_seconds + ?,
                    locked_seconds = locked_seconds + ?,
                    no_session_seconds = no_session_seconds + ?,
                    sleep_seconds = sleep_seconds + ?,
                    shutdown_seconds = shutdown_seconds + ?,
                    unknown_seconds = unknown_seconds + ?
                WHERE date = ?
            """
            await db.execute(update_query, (
                data.get("active", 0),
                data.get("idle", 0),
                data.get("locked", 0),
                data.get("no_session", 0),
                data.get("sleep", 0),
                data.get("shutdown", 0),
                data.get("unknown", 0),
                stats_date
            ))
        else:
            insert_query = """
                INSERT INTO daily_stats (
                    date, active_seconds, idle_seconds, locked_seconds, 
                    no_session_seconds, sleep_seconds, shutdown_seconds, unknown_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            await db.execute(insert_query, (
                stats_date,
                data.get("active", 0),
                data.get("idle", 0),
                data.get("locked", 0),
                data.get("no_session", 0),
                data.get("sleep", 0),
                data.get("shutdown", 0),
                data.get("unknown", 0)
            ))
        await db.commit()

async def get_all_stats() -> List[Dict[str, Any]]:
    """Получение всей статистики, отсортированной по дате DESC."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM daily_stats ORDER BY date DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
