import os
import subprocess
import sys

def build():
    # Проверяем наличие Nuitka
    try:
        import nuitka
    except ImportError:
        print("Nuitka не установлена. Установите ее: pip install nuitka")
        return

    # Основной файл приложения
    main_file = "main.py"
    
    # Имя выходного файла
    output_name = "PAcT"

    # Создаем папку dist, если ее нет
    dist_dir = "dist"
    if not os.path.exists(dist_dir):
        os.makedirs(dist_dir)

    # Базовые флаги
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        "--windows-console-mode=disable",
        f"--output-dir={dist_dir}",
        f"--output-filename={output_name}",
        "--include-data-dir=templates=templates",
        "--include-data-dir=static=static",
        "--follow-imports",
        main_file, # Добавляем основной файл
    ]

    # Проверка UPX
    import shutil
    if shutil.which("upx"):
        cmd.append("--plugin-enable=upx")
        print("UPX найден и будет использован для сжатия.")
    else:
        print("UPX не найден, сборка будет выполнена без сжатия (это нормально).")

    # Иконка
    if os.path.exists("app_icon.ico"):
        cmd.append("--windows-icon-from-ico=app_icon.ico")

    # Специфичные для FastAPI/Uvicorn зависимости
    # Nuitka обычно хорошо справляется сама, но иногда нужны подсказки
    cmd.extend([
        "--include-package=uvicorn",
        "--include-package=fastapi",
        "--include-package=aiosqlite",
        "--include-package=jinja2",
    ])

    # Запуск сборки
    print(f"Запуск сборки {output_name} через Nuitka...")
    print("Это может занять значительное время (особенно в режиме --onefile).")
    print(f"Команда: {' '.join(cmd)}")
    
    if "--dry-run" in sys.argv:
        print("Dry-run: сборка не запущена.")
        return

    try:
        subprocess.run(cmd, check=True)
        print(f"\nСборка успешно завершена! Файл: {dist_dir}\\{output_name}.exe")
    except subprocess.CalledProcessError as e:
        print(f"\nОшибка при сборке: {e}")

if __name__ == "__main__":
    build()
