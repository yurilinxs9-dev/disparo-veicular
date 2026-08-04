# src/disparo/eventos.py
from __future__ import annotations

import sqlite3
from datetime import datetime


def registrar(conn: sqlite3.Connection, tipo: str, texto: str, agora: datetime,
              lead_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO eventos (tipo, lead_id, texto, criado_em) VALUES (?, ?, ?, ?)",
        (tipo, lead_id, texto, agora.isoformat()),
    )
    conn.commit()


def listar(conn: sqlite3.Connection, limite: int = 50) -> list[dict]:
    linhas = conn.execute(
        "SELECT id, tipo, lead_id, texto, criado_em FROM eventos "
        "ORDER BY id DESC LIMIT ?",
        (limite,),
    ).fetchall()
    return [dict(linha) for linha in linhas]
