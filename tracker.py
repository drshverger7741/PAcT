import asyncio
import ctypes
import ctypes.wintypes
import threading
import time
import logging
from datetime import date, datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# WinAPI Constants
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WTS_SESSION_LOGON = 0x5
WTS_SESSION_LOGOFF = 0x6
WM_WTSSESSION_CHANGE = 0x02B1
NOTIFY_FOR_THIS_SESSION = 0

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007

# WinAPI Structures
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HANDLE),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
    ]

# WinAPI Functions
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
wtsapi32 = ctypes.windll.wtsapi32

user32.DefWindowProcW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong

class ActivityTracker:
    def __init__(self, db_module):
        self.db = db_module
        self.current_state = "unknown"
        self.idle_threshold = 60.0
        self.check_interval = 5.0
        self.flush_interval = 30.0
        self.stats = {
            "active": 0.0,
            "idle": 0.0,
            "locked": 0.0,
            "no_session": 0.0,
            "sleep": 0.0,
            "shutdown": 0.0,
            "unknown": 0.0
        }
        self.last_flush_time = time.time()
        self.last_tick_time = time.time()
        self.event_queue = asyncio.Queue()
        self.running = False
        self._lock = threading.Lock()
        self.today = date.today().isoformat()

    def get_idle_time(self) -> float:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = kernel32.GetTickCount() - lii.dwTime
            return millis / 1000.0
        return 0.0

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE:
            if wparam == WTS_SESSION_LOCK:
                self.event_queue.put_nowait(("state_change", "locked"))
            elif wparam == WTS_SESSION_UNLOCK:
                self.event_queue.put_nowait(("state_change", "active"))
            elif wparam == WTS_SESSION_LOGOFF:
                self.event_queue.put_nowait(("state_change", "no_session"))
            elif wparam == WTS_SESSION_LOGON:
                self.event_queue.put_nowait(("state_change", "active"))
        elif msg == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                self.event_queue.put_nowait(("sleep_start", time.time()))
            elif wparam == PBT_APMRESUMESUSPEND:
                self.event_queue.put_nowait(("sleep_end", time.time()))
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create_message_window(self):
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)(self._window_proc)
        wc.lpszClassName = "ActivityTrackerMessageWindow"
        wc.hInstance = kernel32.GetModuleHandleW(None)
        
        user32.RegisterClassW(ctypes.byref(wc))
        hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "Tracker", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, 0)
        
        wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION)
        
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    async def flush_to_db(self):
        with self._lock:
            to_flush = self.stats.copy()
            for k in self.stats: self.stats[k] = 0.0
        
        if any(to_flush.values()):
            await self.db.upsert_daily_stats(self.today, to_flush)
        self.last_flush_time = time.time()

    async def run(self):
        self.running = True
        self.last_tick_time = time.time()
        
        # Загрузка настроек
        self.idle_threshold = float(await self.db.get_setting("idle_threshold", "60"))
        self.check_interval = float(await self.db.get_setting("check_interval", "5"))
        self.flush_interval = float(await self.db.get_setting("flush_interval", "30"))

        # Запуск потока для WinAPI сообщений
        threading.Thread(target=self._create_message_window, daemon=True).start()

        # Определение времени выключения (разрыв между текущим стартом и последней записью)
        # В данной реализации мы просто сбрасываем состояние при старте.
        # Точный расчет выключения требует чтения последней записи из БД.
        all_stats = await self.db.get_all_stats()
        if all_stats:
            last_date_str = all_stats[0]['date']
            if last_date_str == self.today:
                # Если сегодня уже были записи, мы не можем легко определить shutdown_seconds 
                # без хранения времени последней активности. Для простоты пропустим этот шаг.
                pass

        sleep_start_time = None

        while self.running:
            try:
                # Обработка событий из очереди
                try:
                    event = await asyncio.wait_for(self.event_queue.get(), timeout=self.check_interval)
                    if event[0] == "state_change":
                        await self.flush_to_db()
                        self.current_state = event[1]
                    elif event[0] == "sleep_start":
                        await self.flush_to_db()
                        self.current_state = "sleep"
                        sleep_start_time = event[1]
                    elif event[0] == "sleep_end":
                        if sleep_start_time:
                            delta = event[1] - sleep_start_time
                            with self._lock:
                                self.stats["sleep"] += delta
                            sleep_start_time = None
                        self.current_state = "active"
                        await self.flush_to_db()
                except asyncio.TimeoutError:
                    pass

                # Обновление даты
                new_today = date.today().isoformat()
                if new_today != self.today:
                    await self.flush_to_db()
                    self.today = new_today

                # Поллинг активности, если не заблокирован и не спим
                now = time.time()
                delta = now - self.last_tick_time
                self.last_tick_time = now

                if self.current_state not in ["locked", "no_session", "sleep"]:
                    idle_time = self.get_idle_time()
                    if idle_time >= self.idle_threshold:
                        self.current_state = "idle"
                    else:
                        self.current_state = "active"
                
                with self._lock:
                    self.stats[self.current_state] += delta

                # Flush по таймеру
                if now - self.last_flush_time >= self.flush_interval:
                    await self.flush_to_db()

            except Exception as e:
                logger.exception(f"Tracker error: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        self.running = False
        await self.flush_to_db()
