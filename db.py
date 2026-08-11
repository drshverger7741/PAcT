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
                unknown_seconds REAL DEFAULT 0,
                comment TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                start_time TEXT,
                end_time TEXT,
                state TEXT,
                comment TEXT
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
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('visible_columns', 'active_seconds,idle_seconds,locked_seconds,no_session_seconds,sleep_seconds,shutdown_seconds,unknown_seconds')")
        
        # Миграции
        async with db.execute("PRAGMA table_info(daily_stats)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if 'comment' not in columns:
                await db.execute("ALTER TABLE daily_stats ADD COLUMN comment TEXT")
        
        async with db.execute("PRAGMA table_info(activity_log)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if 'comment' not in columns:
                await db.execute("ALTER TABLE activity_log ADD COLUMN comment TEXT")
        
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

async def add_activity_interval(date_str: str, start_time: str, end_time: str, state: str):
    """Добавление интервала активности."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO activity_log (date, start_time, end_time, state) VALUES (?, ?, ?, ?)",
            (date_str, start_time, end_time, state)
        )
        await db.commit()

async def get_activity_log(date_str: str) -> List[Dict[str, Any]]:
    """Получение лога активности за день."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM activity_log WHERE date = ? ORDER BY start_time DESC", (date_str,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def update_day_comment(date_str: str, comment: str):
    """Обновление комментария к дню."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Пытаемся обновить. Если записи нет - вставляем.
        # Используем INSERT OR IGNORE для создания записи с нулями, а потом UPDATE comment
        # Или просто INSERT OR REPLACE, но тогда мы можем затереть статистику, если не укажем все поля.
        # Безопаснее сначала проверить существование.
        async with db.execute("SELECT 1 FROM daily_stats WHERE date = ?", (date_str,)) as cursor:
            row = await cursor.fetchone()
        
        if row:
            await db.execute("UPDATE daily_stats SET comment = ? WHERE date = ?", (comment, date_str))
        else:
            await db.execute("INSERT INTO daily_stats (date, comment, active_seconds, idle_seconds, locked_seconds, no_session_seconds, sleep_seconds, shutdown_seconds, unknown_seconds) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0)", (date_str, comment))
        await db.commit()

async def update_interval_comment(interval_id: int, comment: str):
    """Обновление комментария к интервалу."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE activity_log SET comment = ? WHERE id = ?", (comment, interval_id))
        await db.commit()
