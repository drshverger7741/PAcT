import asyncio
import signal
import socket
import sys
import os
import uvicorn
import db
from tracker import ActivityTracker
from web import app, init_web

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

async def main():
    # Инициализация БД
    await db.init_db()
    
    # Инициализация трекера
    tracker = ActivityTracker(db)
    
    # Выбор порта
    port = 8765
    while is_port_in_use(port):
        port += 1
    
    stop_event = asyncio.Event()

    # Инициализация веб-интерфейса
    init_web(tracker, db, stop_event)
    
    print(f"Starting server on http://localhost:{port}")
    
    # Настройка логов для uvicorn в режиме без консоли
    log_config = uvicorn.config.LOGGING_CONFIG.copy()
    if getattr(sys, 'frozen', False) or os.path.basename(sys.executable).lower() == 'pythonw.exe':
        # В режиме без консоли (noconsole/pythonw) sys.stdout/stderr могут быть None или закрыты,
        # что вызывает ошибки в логгере uvicorn (isatty).
        # Перенаправляем стандартные потоки в никуда, если они None.
        if sys.stdout is None:
            sys.stdout = open(os.devnull, 'w')
        if sys.stderr is None:
            sys.stderr = open(os.devnull, 'w')

        # Отключаем цвета и интерактивность, которые требуют TTY
        if "formatters" in log_config:
            if "default" in log_config["formatters"]:
                log_config["formatters"]["default"]["use_colors"] = False
            if "access" in log_config["formatters"]:
                log_config["formatters"]["access"]["use_colors"] = False
    
    # Конфигурация Uvicorn
    config = uvicorn.Config(
        app, 
        host="127.0.0.1", 
        port=port, 
        log_level="info",
        log_config=log_config
    )
    server = uvicorn.Server(config)
    
    # Запуск задач
    tracker_task = asyncio.create_task(tracker.run())
    
    # Обработка остановки
    loop = asyncio.get_running_loop()
    
    def signal_handler():
        print("\nShutdown signal received...")
        stop_event.set()

    # В Windows signal.SIGINT работает специфично, но для asyncio loop.add_signal_handler не поддерживается для SIGINT
    # Мы будем использовать проверку stop_event
    
    async def run_server():
        await server.serve()
        stop_event.set()

    server_task = asyncio.create_task(run_server())
    
    # Ожидание сигнала остановки или завершения сервера
    await stop_event.wait()
    
    # Graceful shutdown
    print("Stopping tracker...")
    await tracker.stop()
    tracker_task.cancel()
    
    print("Stopping server...")
    server.should_exit = True
    await server_task
    
    print("Done.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
