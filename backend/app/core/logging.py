import logging
import os

BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # goes up from core/
LOG_DIR = os.path.join(BASE_DIR, "logs")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

os.makedirs(LOG_DIR, exist_ok=True)


class _PollAccessFilter(logging.Filter):
    """Drop uvicorn access lines for hot, low-value poll/probe paths.

    - GET /api/notebooks/{id}/files: the frontend refetches every 2s while
      ingestion is processing; each hit would otherwise spam console/app.log.
    - GET /api/health with 200: compose HEALTHCHECK hits every 10s.
      Non-200 health lines still pass so failures stay visible.
    Failures still surface via each endpoint's logger.exception.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - exotic records must never break logging
            return True
        if "GET /api/notebooks/" in msg and "/files HTTP/" in msg:
            return False
        return not ("GET /api/health HTTP/" in msg and '" 200 ' in msg)


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
