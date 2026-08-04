# src/disparo/cota.py
from __future__ import annotations

import sqlite3
from datetime import date

CHAVE_INICIO = "inicio_operacao"

RAMPA = ((3, 10), (7, 20))  # (dias_ate, limite); acima disso, TETO
TETO = 30


def definir_inicio(conn: sqlite3.Connection, dia: date) -> None:
    conn.execute(
        "INSERT INTO config (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (CHAVE_INICIO, dia.isoformat()),
    )
    conn.commit()


def _inicio(conn: sqlite3.Connection) -> date | None:
    linha = conn.execute(
        "SELECT valor FROM config WHERE chave = ?", (CHAVE_INICIO,)
    ).fetchone()
    return date.fromisoformat(linha["valor"]) if linha else None


def limite_do_dia(conn: sqlite3.Connection, hoje: date) -> int:
    inicio = _inicio(conn)
    if inicio is None or hoje < inicio:
        return 0
    dias = (hoje - inicio).days
    for ate, limite in RAMPA:
        if dias < ate:
            return limite
    return TETO


def enviados_no_dia(conn: sqlite3.Connection, hoje: date) -> int:
    linha = conn.execute(
        "SELECT quantidade FROM envios_diarios WHERE dia = ?", (hoje.isoformat(),)
    ).fetchone()
    return linha["quantidade"] if linha else 0


def registrar_envio(conn: sqlite3.Connection, hoje: date) -> None:
    conn.execute(
        "INSERT INTO envios_diarios (dia, quantidade) VALUES (?, 1) "
        "ON CONFLICT(dia) DO UPDATE SET quantidade = quantidade + 1",
        (hoje.isoformat(),),
    )
    conn.commit()


def tem_cota(conn: sqlite3.Connection, hoje: date) -> bool:
    return enviados_no_dia(conn, hoje) < limite_do_dia(conn, hoje)
