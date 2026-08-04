# src/disparo/disjuntor.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from disparo.eventos import registrar

CHAVE_PAUSADO = "pausado"
MINIMO_PARA_AVALIAR = 20
PISO_RESPOSTA = 0.10
TETO_OPT_OUT = 3

_RESPONDERAM = ("em_conversa", "quente", "frio", "opt_out", "dado_desatualizado")


@dataclass(frozen=True)
class Veredito:
    ok: bool
    motivo: str | None = None


def avaliar(conn: sqlite3.Connection, amostra: int = 50) -> Veredito:
    linhas = conn.execute(
        "SELECT status FROM leads WHERE contatado_em IS NOT NULL "
        "ORDER BY contatado_em DESC LIMIT ?",
        (amostra,),
    ).fetchall()

    status = [linha["status"] for linha in linhas]
    opt_outs = sum(1 for s in status if s == "opt_out")
    if opt_outs >= TETO_OPT_OUT:
        return Veredito(False, f"{opt_outs} opt-out nos últimos {len(status)} disparos")

    if len(status) < MINIMO_PARA_AVALIAR:
        return Veredito(True)

    responderam = sum(1 for s in status if s in _RESPONDERAM)
    taxa = responderam / len(status)
    if taxa < PISO_RESPOSTA:
        return Veredito(
            False, f"taxa de resposta em {taxa:.0%} nos últimos {len(status)} disparos"
        )
    return Veredito(True)


def esta_pausado(conn: sqlite3.Connection) -> bool:
    linha = conn.execute(
        "SELECT valor FROM config WHERE chave = ?", (CHAVE_PAUSADO,)
    ).fetchone()
    return bool(linha) and linha["valor"] == "1"


def _definir(conn: sqlite3.Connection, valor: str) -> None:
    conn.execute(
        "INSERT INTO config (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (CHAVE_PAUSADO, valor),
    )
    conn.commit()


def pausar(conn: sqlite3.Connection, motivo: str, agora: datetime) -> None:
    _definir(conn, "1")
    registrar(conn, "alerta", f"Operação pausada: {motivo}", agora)


def retomar(conn: sqlite3.Connection, agora: datetime) -> None:
    _definir(conn, "0")
    registrar(conn, "sistema", "Operação retomada", agora)
