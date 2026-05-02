"""
db.py — Camada de compatibilidade SQLite (local) / PostgreSQL (Render)
Não altere este arquivo. Ele é importado pelo app.py no lugar do sqlite3.

- Em produção (Render): usa DATABASE_URL com pg8000 (puro Python, sem binário)
- Em desenvolvimento local: usa sqlite3 normalmente
"""

import os
import re
import urllib.parse

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Render pode fornecer "postgres://..." — normaliza para "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

USE_POSTGRES = DATABASE_URL.startswith("postgresql://")


def _adapt_sql(sql: str) -> str:
    """Converte SQL dialeto SQLite → PostgreSQL quando necessário."""
    if not USE_POSTGRES:
        return sql

    # 1. Placeholders: ? → %s
    sql = sql.replace("?", "%s")

    # 2. strftime('%Y-%m', campo) → TO_CHAR(campo::date, 'YYYY-MM')
    sql = re.sub(
        r"strftime\('%Y-%m',\s*([^)]+)\)",
        lambda m: f"TO_CHAR({m.group(1).strip()}::date, 'YYYY-MM')",
        sql,
    )

    return sql


class _PgRow(dict):
    """
    Emula sqlite3.Row: acesso por chave (row['campo']) e por índice (row[0]).
    Também suporta dict(row) que o app.py usa extensivamente.
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _CursorWrapper:
    """Cursor que adapta SQL automaticamente e devolve _PgRow nas buscas."""

    def __init__(self, cursor):
        self._cur = cursor

    def execute(self, sql, params=None):
        sql = _adapt_sql(sql)
        if params is None:
            self._cur.execute(sql)
        else:
            self._cur.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description]
        return _PgRow(zip(cols, row))

    def fetchall(self):
        rows = self._cur.fetchall()
        cols = [d[0] for d in self._cur.description]
        return [_PgRow(zip(cols, r)) for r in rows]

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _ConnWrapper:
    """
    Conexão que emula a interface do sqlite3:
    - conn.execute(sql, params) → igual ao sqlite3
    - conn.cursor() → cursor adaptado
    - conn.commit() / conn.close()
    - conn.row_factory é ignorado (já tratamos internamente)
    """

    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None  # aceita a atribuição do app.py sem erro

    def cursor(self):
        return _CursorWrapper(self._conn.cursor())

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, script: str):
        """
        sqlite3 tem executescript(); pg8000 não.
        Divide o script em statements e executa um a um,
        convertendo AUTOINCREMENT → SERIAL do PostgreSQL.
        """
        if USE_POSTGRES:
            script = script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur = self._conn.cursor()
            for stmt in script.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            self._conn.commit()
        else:
            self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# -------------------------------------------------------------------
# Ponto de entrada público — substitui sqlite3.connect() no app.py
# -------------------------------------------------------------------

def connect():
    """
    Retorna uma conexão compatível com a interface sqlite3.
    """
    if USE_POSTGRES:
        import pg8000.dbapi
        p = urllib.parse.urlparse(DATABASE_URL)
        conn_raw = pg8000.dbapi.connect(
            user=p.username,
            password=p.password,
            host=p.hostname,
            port=p.port or 5432,
            database=p.path.lstrip("/"),
            ssl_context=True,
        )
        return _ConnWrapper(conn_raw)
    else:
        import sqlite3
        db_path = os.environ.get("DB_PATH", "integrare.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
