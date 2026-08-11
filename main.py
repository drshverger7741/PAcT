import asyncio
import signal
import socket
import sys
import os
import uvicorn
import logging
import db
from tracker import ActivityTracker
from web import app, init_web

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(db.LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout) if sys.stdout else logging.NullHandler()
    ]
)
logger = logging.getLogger(__name__)

class LoggerWriter:
    def __init__(self, level):
        self.level = level
    def write(self, message):
        if message.strip():
            self.level(message.strip())
    def flush(self):
        pass

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
    
    logger.info(f"Starting server on http://localhost:{port}")
    
    # Перенаправление stdout/stderr в логгер для отлова print и ошибок
    sys.stdout = LoggerWriter(logger.info)
    sys.stderr = LoggerWriter(logger.error)

    # Настройка логов для uvicorn
    log_config = uvicorn.config.LOGGING_CONFIG.copy()
    
    # Отключаем цвета, так как они плохо пишутся в файл
    if "formatters" in log_config:
        if "default" in log_config["formatters"]:
            log_config["formatters"]["default"]["use_colors"] = False
        if "access" in log_config["formatters"]:
            log_config["formatters"]["access"]["use_colors"] = False
    
    # Добавляем наш FileHandler в конфиг uvicorn
    log_config["handlers"]["file"] = {
        "class": "logging.FileHandler",
        "filename": db.LOG_PATH,
        "encoding": "utf-8",
        "formatter": "default",
    }
    log_config["loggers"]["uvicorn"]["handlers"].append("file")
    log_config["loggers"]["uvicorn.error"]["handlers"].append("file")
    log_config["loggers"]["uvicorn.access"]["handlers"].append("file")

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
        logger.info("Shutdown signal received...")
        stop_event.set()

    # В Windows signal.SIGINT работает специфично
    
    async def run_server():
        await server.serve()
        stop_event.set()

    server_task = asyncio.create_task(run_server())
    
    # Ожидание сигнала остановки или завершения сервера
    await stop_event.wait()
    
    # Graceful shutdown
    logger.info("Stopping tracker...")
    await tracker.stop()
    tracker_task.cancel()
    
    logger.info("Stopping server...")
    server.should_exit = True
    await server_task
    
    logger.info("Done.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
