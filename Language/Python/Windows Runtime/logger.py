from pathlib import Path
from datetime import datetime


LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_FILE = LOG_DIR / "runtime.log"


def _write(level, message):
    LOG_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}\n"

    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(line)


def info(message):
    _write("INFO", message)


def warning(message):
    _write("WARNING", message)


def error(message):
    _write("ERROR", message)