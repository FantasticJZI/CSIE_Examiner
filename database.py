import sqlite3
from datetime import date

class StudyDB:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            # 使用者 XP 表
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    xp INTEGER DEFAULT 0,
                    last_answered DATE
                )
            """)

    def get_user(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT xp, last_answered FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()

    def add_xp(self, user_id, xp_gain):
        today = date.today().isoformat()
        with self.conn:
            self.conn.execute("""
                INSERT INTO users (user_id, xp, last_answered) 
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    xp = xp + ?, 
                    last_answered = ?
            """, (user_id, xp_gain, today, xp_gain, today))