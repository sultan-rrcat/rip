from dotenv import load_dotenv
import os
from contextlib import contextmanager
import psycopg2
from neo4j import GraphDatabase

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

@contextmanager
def pg_connection():
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        yield conn
        conn.commit()  # ✅ commit if everything went fine

    except Exception as e:
        if conn:
            conn.rollback()  # ✅ rollback on failure
        print(f"Error connecting to database: {e}")
        print(
            f"DB Credentials -> "
            f"Host: {DB_HOST}, "
            f"Port: {DB_PORT}, "
            f"Name: {DB_NAME}, "
            f"User: {DB_USER}, "
            f"Password: {DB_PASSWORD}"
        )
        raise

    finally:
        if conn:
            conn.close()  # ✅ always close connection


def neo4j_driver():
    return GraphDatabase.driver(
        NEO4J_URI,
        auth = (NEO4J_USER, NEO4J_PASSWORD)
    )