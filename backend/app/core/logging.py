import logging
import os

BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # goes up from core/
LOG_DIR = os.path.join(BASE_DIR, "logs")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

os.makedirs(LOG_DIR, exist_ok=True)


class _PollAccessFilter(logging.Filter):
    """Drop uvicorn access lines for the hot file-list poll path.

    The frontend refetches GET /api/notebooks/{id}/files every 2s while
    ingestion is processing; each hit would otherwise spam console/app.log.
    Failures still surface via the endpoint's logger.exception.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - exotic records must never break logging
            return True
        return not ("GET /api/notebooks/" in msg and "/files HTTP/" in msg)


def setup_logging():
    logger = logging.getLogger()

    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.INFO)
    log_format = logging.Formatter(
        "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
    )

    file_handler = logging.FileHandler(
        os.path.join(LOG_DIR, "app.log"), encoding="utf-8"
    )
    file_handler.setFormatter(log_format)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Silence uvicorn's per-request access line for the file-list poll path.
    # Attached here (not via CLI flags) so docker + host-local launches
    # both get it; the filter lives on the logger, independent of handlers.
    logging.getLogger("uvicorn.access").addFilter(_PollAccessFilter())

    return logger
