import asyncio
import threading
import time
import logging
import os
from datetime import date, datetime
import winapi_utils as winapi

logger = logging.getLogger(__name__)

class ActivityTracker:
    def __init__(self, db_module):
        self.db = db_module
        self.current_state = "unknown"
        self.idle_threshold = 300.0
        self.check_interval = 10.0
        self.flush_interval = 60.0
        self.app_name = "PAcT"
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
        self.last_state_change_time = time.time()
        self.event_queue = asyncio.Queue()
        self.running = False
        self.paused = False
        self.is_flushing = False
        self._lock = threading.Lock()
        self.today = date.today().isoformat()
        self.track_window_activity = True
        
        # Window tracking
        self.current_window = {"title": "", "app": "", "start_time": 0.0}
        self.window_buffer = [] # List of (title, app, start_time, end_time, duration)

    def get_idle_time(self) -> float:
        lii = winapi.LASTINPUTINFO()
        lii.cbSize = winapi.sizeof(winapi.LASTINPUTINFO)
        if winapi.user32.GetLastInputInfo(winapi.byref(lii)):
            millis = winapi.kernel32.GetTickCount() - lii.dwTime
            return millis / 1000.0
        return 0.0

    def get_active_window_info(self):
        hwnd = winapi.user32.GetForegroundWindow()
        if not hwnd:
            return None, None
        
        # Заголовок окна
        length = 512
        buf = winapi.create_unicode_buffer(length)
        winapi.user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value
        
        # Имя приложения (exe)
        pid = winapi.ctypes.wintypes.DWORD()
        winapi.user32.GetWindowThreadProcessId(hwnd, winapi.byref(pid))
        
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        handle = winapi.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        app_name = "Unknown"
        if handle:
            buf = winapi.create_unicode_buffer(length)
            if winapi.psapi.GetModuleFileNameExW(handle, 0, buf, length):
                app_name = os.path.basename(buf.value)
            winapi.kernel32.CloseHandle(handle)
        
        return title, app_name

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == winapi.WM_WTSSESSION_CHANGE:
            idle_time = self.get_idle_time()
            if wparam == winapi.WTS_SESSION_LOCK:
                self.event_queue.put_nowait(("state_change", "locked", idle_time))
            elif wparam == winapi.WTS_SESSION_UNLOCK:
                self.event_queue.put_nowait(("state_change", "active", idle_time))
            elif wparam == winapi.WTS_SESSION_LOGOFF:
                self.event_queue.put_nowait(("state_change", "no_session", idle_time))
            elif wparam == winapi.WTS_SESSION_LOGON:
                self.event_queue.put_nowait(("state_change", "active", idle_time))
        elif msg == winapi.WM_POWERBROADCAST:
            if wparam == winapi.PBT_APMSUSPEND:
                self.event_queue.put_nowait(("sleep_start", time.time(), self.get_idle_time()))
            elif wparam == winapi.PBT_APMRESUMESUSPEND:
                self.event_queue.put_nowait(("sleep_end", time.time()))
        return winapi.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create_message_window(self):
        wc = winapi.WNDCLASSW()
        wc.lpfnWndProc = winapi.WINFUNCTYPE(winapi.c_longlong, winapi.ctypes.wintypes.HWND, winapi.ctypes.wintypes.UINT, winapi.ctypes.wintypes.WPARAM, winapi.ctypes.wintypes.LPARAM)(self._window_proc)
        wc.lpszClassName = "ActivityTrackerMessageWindow"
        wc.hInstance = winapi.kernel32.GetModuleHandleW(None)
        
        winapi.user32.RegisterClassW(winapi.byref(wc))
        hwnd = winapi.user32.CreateWindowExW(0, wc.lpszClassName, "Tracker", 0, 0, 0, 0, 0, 0, None, wc.hInstance, None)
        
        winapi.wtsapi32.WTSRegisterSessionNotification(hwnd, winapi.NOTIFY_FOR_THIS_SESSION)
        
        msg = winapi.ctypes.wintypes.MSG()
        while winapi.user32.GetMessageW(winapi.byref(msg), 0, 0, 0) > 0:
            winapi.user32.TranslateMessage(winapi.byref(msg))
            winapi.user32.DispatchMessageW(winapi.byref(msg))

    async def log_interval(self, state, start_time, end_time):
        """Логирует интервал в БД."""
        d = datetime.fromtimestamp(start_time).date().isoformat()
        st = datetime.fromtimestamp(start_time).strftime("%H:%M:%S")
        et = datetime.fromtimestamp(end_time).strftime("%H:%M:%S")
        await self.db.add_activity_interval(d, st, et, state)

    async def change_state(self, new_state, override_time=None):
        """Меняет состояние и логирует интервал."""
        if new_state == self.current_state:
            return
        
        transition_time = override_time if override_time is not None else time.time()
        await self.log_interval(self.current_state, self.last_state_change_time, transition_time)
        await self.flush_to_db()
        self.current_state = new_state
        self.last_state_change_time = transition_time

    async def flush_to_db(self):
        if self.is_flushing:
            return
        self.is_flushing = True
        try:
            with self._lock:
                to_flush = self.stats.copy()
                for k in self.stats: self.stats[k] = 0.0
                
                # Flush windows
                windows_to_flush = self.window_buffer.copy()
                self.window_buffer = []
            
            if any(to_flush.values()):
                await self.db.upsert_daily_stats(self.today, to_flush)
                
            if self.track_window_activity:
                for win in windows_to_flush:
                    await self.db.add_window_activity(self.today, win[0], win[1], win[2], win[3], win[4])
                
            self.last_flush_time = time.time()
        finally:
            self.is_flushing = False

    async def run(self):
        self.running = True
        self.last_tick_time = time.time()
        self.last_state_change_time = time.time()
        
        # Загрузка настроек
        self.idle_threshold = float(await self.db.get_setting("idle_threshold", "300"))
        self.check_interval = float(await self.db.get_setting("check_interval", "10"))
        self.flush_interval = float(await self.db.get_setting("flush_interval", "60"))
        self.track_window_activity = (await self.db.get_setting("track_window_activity", "true")).lower() == "true"

        # Начальное состояние: проверяем активность сразу
        idle_time = self.get_idle_time()
        if idle_time >= self.idle_threshold:
            self.current_state = "idle"
        else:
            self.current_state = "active"

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
                        new_state = event[1]
                        idle_time = event[2]
                        now = time.time()
                        real_transition_time = now - idle_time
                        
                        # Корректируем stats: переносим время бездействия в новое состояние
                        time_to_move = now - real_transition_time
                        with self._lock:
                            self.stats[self.current_state] -= time_to_move
                            self.stats[new_state] += time_to_move
                            
                        await self.change_state(new_state, override_time=real_transition_time)
                    elif event[0] == "sleep_start":
                        event_time = event[1]
                        idle_time = event[2]
                        real_sleep_start = event_time - idle_time
                        
                        # Корректируем stats: отнимаем время с последней активности от текущего стейта.
                        # Мы не прибавляем его в sleep здесь, так как вся дельта сна 
                        # будет добавлена при получении sleep_end.
                        time_to_move = event_time - real_sleep_start
                        with self._lock:
                            self.stats[self.current_state] -= time_to_move
                            
                        await self.change_state("sleep", override_time=real_sleep_start)
                        sleep_start_time = real_sleep_start
                    elif event[0] == "sleep_end":
                        if sleep_start_time:
                            delta = event[1] - sleep_start_time
                            with self._lock:
                                self.stats["sleep"] += delta
                            sleep_start_time = None
                        await self.change_state("active")
                        # Обновляем last_tick_time, чтобы delta после сна не включала время сна
                        self.last_tick_time = time.time()
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

                if self.paused:
                    # Если на паузе, просто обновляем last_tick_time и пропускаем логику
                    continue

                if self.current_state not in ["locked", "no_session", "sleep"]:
                    idle_time = self.get_idle_time()
                    if idle_time >= self.idle_threshold:
                        if self.current_state != "idle":
                            # Переход в простой: считаем начало с момента последней активности
                            real_idle_start = now - idle_time
                            
                            # Корректируем stats: отнимаем ошибочно начисленное активное время
                            # и переносим его в idle
                            time_to_move = now - real_idle_start
                            with self._lock:
                                self.stats[self.current_state] -= time_to_move
                                self.stats["idle"] += time_to_move
                            
                            await self.change_state("idle", override_time=real_idle_start)
                            
                            # Закрываем окно при уходе в idle
                            if self.track_window_activity and self.current_window["start_time"] > 0:
                                st_str = datetime.fromtimestamp(self.current_window["start_time"]).strftime("%H:%M:%S")
                                et_str = datetime.fromtimestamp(real_idle_start).strftime("%H:%M:%S")
                                dur = real_idle_start - self.current_window["start_time"]
                                if dur > 0:
                                    self.window_buffer.append((self.current_window["title"], self.current_window["app"], st_str, et_str, dur))
                                self.current_window = {"title": "", "app": "", "start_time": 0.0}
                    else:
                        await self.change_state("active")
                        
                        # Мониторинг окон только в активном состоянии
                        if self.track_window_activity:
                            title, app = self.get_active_window_info()
                            if title != self.current_window["title"] or app != self.current_window["app"]:
                                # Смена окна
                                if self.current_window["start_time"] > 0:
                                    st_str = datetime.fromtimestamp(self.current_window["start_time"]).strftime("%H:%M:%S")
                                    et_str = datetime.fromtimestamp(now).strftime("%H:%M:%S")
                                    dur = now - self.current_window["start_time"]
                                    if dur > 0:
                                        # Проверка на слияние с предыдущим (если в буфере то же самое)
                                        if self.window_buffer and self.window_buffer[-1][0] == self.current_window["title"] and self.window_buffer[-1][1] == self.current_window["app"]:
                                            prev = self.window_buffer.pop()
                                            self.window_buffer.append((prev[0], prev[1], prev[2], et_str, prev[4] + dur))
                                        else:
                                            self.window_buffer.append((self.current_window["title"], self.current_window["app"], st_str, et_str, dur))
                                
                                self.current_window = {"title": title or "Unknown", "app": app or "Unknown", "start_time": now}
                            else:
                                # То же окно, просто обновляем длительность при флаше или периодически? 
                                # В нашей схеме мы просто ждем смены или флаша. 
                                # Чтобы данные не терялись при долгом сидении в одном окне, 
                                # будем периодически обновлять end_time в буфере.
                                if self.current_window["start_time"] > 0:
                                    st_str = datetime.fromtimestamp(self.current_window["start_time"]).strftime("%H:%M:%S")
                                    et_str = datetime.fromtimestamp(now).strftime("%H:%M:%S")
                                    dur = now - self.current_window["start_time"]
                                    # Мы не добавляем в буфер здесь, а просто "держим" в current_window
                                    pass

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
        now = time.time()
        await self.log_interval(self.current_state, self.last_state_change_time, now)
        await self.flush_to_db()

    def pause(self):
        if not self.paused:
            self.paused = True
            logging.info("Monitoring paused")
            # Закрываем текущее окно и интервал при уходе на паузу
            asyncio.run_coroutine_threadsafe(self._handle_pause_stop(), asyncio.get_event_loop())

    async def _handle_pause_stop(self):
        now = time.time()
        await self.log_interval(self.current_state, self.last_state_change_time, now)
        
        if self.track_window_activity and self.current_window["start_time"] > 0:
            st_str = datetime.fromtimestamp(self.current_window["start_time"]).strftime("%H:%M:%S")
            et_str = datetime.fromtimestamp(now).strftime("%H:%M:%S")
            dur = now - self.current_window["start_time"]
            if dur > 0:
                self.window_buffer.append((self.current_window["title"], self.current_window["app"], st_str, et_str, dur))
            self.current_window = {"title": "", "app": "", "start_time": 0.0}
            
        await self.flush_to_db()
        self.last_state_change_time = now

    def resume(self):
        if self.paused:
            self.paused = False
            self.last_tick_time = time.time()
            logging.info("Monitoring resumed")
