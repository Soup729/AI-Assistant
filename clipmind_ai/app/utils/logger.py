import os
import sys

from loguru import logger

from app.utils.runtime_paths import get_user_data_dir


def setup_logger():
    """Configure application logging."""
    log_dir = get_user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # Windowed PyInstaller apps may not have stderr; only add the console sink when available.
    console_sink = sys.stderr if sys.stderr is not None else sys.__stderr__
    if console_sink is not None:
        logger.add(
            console_sink,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            level="INFO",
        )

    log_candidates = [
        log_dir / "app.log",
        log_dir / f"app-{os.getpid()}.log",
    ]
    for log_path in log_candidates:
        try:
            logger.add(
                log_path,
                rotation="10 MB",
                retention="1 week",
                compression="zip",
                encoding="utf-8",
                level="DEBUG",
            )
            break
        except OSError:
            continue


setup_logger()
