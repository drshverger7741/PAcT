import aiosqlite
import os
import sys
import hashlib
import logging
import shutil
from datetime import date, datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS month_comments (
                year_month TEXT PRIMARY KEY,
                comment TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS week_comments (
                year_week TEXT PRIMARY KEY,
                comment TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS window_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                window_title TEXT,
                app_name TEXT,
                start_time TEXT,
                end_time TEXT,
                duration REAL
            )
        """)
        # Начальные настройки, если их нет
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('idle_threshold', '300')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('activity_grace_period', '5')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('check_interval', '10')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('flush_interval', '60')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('visible_columns', 'active_seconds,idle_seconds,locked_seconds,sleep_seconds')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('language', 'ru')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_backup_enabled', 'False')")
        
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

def hash_password(password: str) -> str:
    """Хэширует пароль для хранения."""
    return hashlib.sha256(password.encode()).hexdigest()

async def verify_password(password: str) -> bool:
    """Проверяет введенный пароль."""
    stored_hash = await get_setting("app_password_hash")
    if not stored_hash:
        return False
    return hash_password(password) == stored_hash

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

async def add_activity_interval(date_str: str, start_time: str, end_time: Optional[str], state: str):
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

async def get_last_activity() -> Dict[str, Any]:
    """Получение последней записи из лога активности."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Для определения времени последнего выхода нам нужны записи, где есть end_time.
        # Startup/Shutdown записи (теперь без end_time) используются для меток.
        async with db.execute("SELECT * FROM activity_log WHERE end_time IS NOT NULL ORDER BY date DESC, end_time DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

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

async def update_month_comment(year_month: str, comment: str):
    """Обновление комментария к месяцу (формат YYYY-MM)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO month_comments (year_month, comment) VALUES (?, ?)", (year_month, comment))
        await db.commit()

async def update_week_comment(year_week: str, comment: str):
    """Обновление комментария к неделе (формат YYYY-WW)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO week_comments (year_week, comment) VALUES (?, ?)", (year_week, comment))
        await db.commit()

async def get_month_comments() -> Dict[str, str]:
    """Получение всех комментариев к месяцам."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT year_month, comment FROM month_comments") as cursor:
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}

async def get_week_comments() -> Dict[str, str]:
    """Получение всех комментариев к неделям."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT year_week, comment FROM week_comments") as cursor:
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}

async def add_window_activity(date_str: str, title: str, app: str, start: str, end: str, duration: float):
    """Добавление записи об активности окна."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO window_activity (date, window_title, app_name, start_time, end_time, duration) VALUES (?, ?, ?, ?, ?, ?)",
            (date_str, title, app, start, end, duration)
        )
        await db.commit()

async def create_backup() -> Optional[str]:
    """Создает бэкап базы данных с помощью VACUUM INTO."""
    backup_dir = os.path.join(get_db_path(), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_filename = f"backup_activity_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_filename)
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Используем VACUUM INTO для безопасного копирования во время работы
            # Путь в Windows может содержать пробелы, поэтому экранируем его
            await db.execute(f"VACUUM INTO '{backup_path}'")
        
        logger.info(f"Backup created: {backup_path}")
        await rotate_backups(backup_dir)
        return backup_filename
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return None

async def rotate_backups(backup_dir: str, keep_days: int = 7):
    """Удаляет старые бэкапы, оставляя только за последние N дней."""
    try:
        backups = []
        for f in os.listdir(backup_dir):
            if f.startswith("backup_activity_") and f.endswith(".db"):
                path = os.path.join(backup_dir, f)
                backups.append((path, os.path.getmtime(path)))
        
        # Сортируем по времени изменения (новые в конце)
        backups.sort(key=lambda x: x[1])
        
        # Оставляем только последние keep_days бэкапов
        if len(backups) > keep_days:
            to_delete = backups[:-keep_days]
            for path, _ in to_delete:
                os.remove(path)
                logger.info(f"Old backup deleted: {path}")
    except Exception as e:
        logger.error(f"Error during backup rotation: {e}")

async def list_backups() -> List[Dict[str, Any]]:
    """Возвращает список доступных бэкапов."""
    backup_dir = os.path.join(get_db_path(), "backups")
    if not os.path.exists(backup_dir):
        return []
    
    backups = []
    for f in os.listdir(backup_dir):
        if f.startswith("backup_activity_") and f.endswith(".db"):
            path = os.path.join(backup_dir, f)
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
            backups.append({
                "filename": f,
                "size": size,
                "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            })
    
    # Сортируем: новые сверху
    backups.sort(key=lambda x: x["filename"], reverse=True)
    return backups

async def restore_backup(filename: str) -> bool:
    """Восстанавливает базу данных из бэкапа."""
    backup_path = os.path.join(get_db_path(), "backups", filename)
    if not os.path.exists(backup_path):
        return False
    
    try:
        # 1. Делаем временный бэкап текущей БД перед восстановлением
        temp_backup = os.path.join(get_db_path(), "activity_monitor_pre_restore.db")
        shutil.copy2(DB_PATH, temp_backup)
        
        # 2. Копируем файл бэкапа поверх текущей БД
        shutil.copy2(backup_path, DB_PATH)
        logger.info(f"Database restored from {filename}")
        return True
    except Exception as e:
        logger.error(f"Failed to restore backup: {e}")
        return False

async def get_window_stats(start_date: str, end_date: str = None) -> List[Dict[str, Any]]:
    """Получение агрегированной статистики по окнам за период."""
    if end_date is None:
        end_date = start_date
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT app_name, window_title, SUM(duration) as total_duration
            FROM window_activity
            WHERE date BETWEEN ? AND ?
            GROUP BY app_name, window_title
            ORDER BY total_duration DESC
        """
        async with db.execute(query, (start_date, end_date)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_window_timeline(start_date: str, end_date: str = None) -> List[Dict[str, Any]]:
    """Получение детального лога активности окон за период."""
    if end_date is None:
        end_date = start_date
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT * FROM window_activity
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC, start_time DESC
        """
        async with db.execute(query, (start_date, end_date)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
