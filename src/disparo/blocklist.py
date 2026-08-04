from __future__ import annotations

import sqlite3
from datetime import datetime


def bloquear(conn: sqlite3.Connection, telefone: str, motivo: str,
             agora: datetime) -> None:
    conn.execute(
        "INSERT INTO blocklist (telefone_e164, motivo, criado_em) VALUES (?, ?, ?) "
        "ON CONFLICT(telefone_e164) DO NOTHING",
        (telefone, motivo, agora.isoformat()),
    )
    conn.commit()


def esta_bloqueado(conn: sqlite3.Connection, telefone: str) -> bool:
    linha = conn.execute(
        "SELECT 1 FROM blocklist WHERE telefone_e164 = ?", (telefone,)
    ).fetchone()
    return linha is not None
