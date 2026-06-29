import asyncio
import sqlite3
import sys

from app.database import async_session
from app.models import Document
from sqlalchemy import delete

async def clear_postgres():
    print("Clearing PostgreSQL...")
    async with async_session() as session:
        try:
            stmt = delete(Document)
            result = await session.execute(stmt)
            await session.commit()
            print(f"Deleted {result.rowcount} records from PostgreSQL.")
        except Exception as e:
            await session.rollback()
            print(f"Error clearing PostgreSQL: {e}")

def clear_sqlite():
    print("Clearing SQLite (doe_scraper.db)...")
    try:
        conn = sqlite3.connect('doe_scraper.db')
        c = conn.cursor()
        c.execute('DELETE FROM documents')
        conn.commit()
        print(f"Deleted {c.rowcount} records from SQLite.")
        conn.close()
    except Exception as e:
        print(f"Error clearing SQLite: {e}")

if __name__ == "__main__":
    clear_sqlite()
    asyncio.run(clear_postgres())
