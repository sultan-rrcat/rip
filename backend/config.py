import logging
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")

ALL_MINILM_L6_V2_MODEL_PATH = r"D:\models\all-MiniLM-L6-v2"


DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")  # default if not set
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")


def pg_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
    except Exception as e:
        print(f"Error connecting to database: {e}")
        print(
            f"DB Credentials -> "
            f"Host: {DB_HOST}, "
            f"Port: {DB_PORT}, "
            f"Name: {DB_NAME}, "
            f"User: {DB_USER}, "
            f"Password: {DB_PASSWORD}"
        )
        return e

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    log_format = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = logging.FileHandler(os.path.join(LOG_DIR, "app.log"))
    file_handler.setFormatter(log_format)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)

    if logger.hasHandlers():
        logger.handlers.clear()

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
