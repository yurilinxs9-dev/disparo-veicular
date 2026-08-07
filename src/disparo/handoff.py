# src/disparo/handoff.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from disparo import eventos
from disparo.conversador import Qualificacao


def avisar_vendedora(conn: sqlite3.Connection, evo, telefone_vendedora: str,
                     lead: sqlite3.Row, qualificacao: Qualificacao,
                     agora: datetime) -> None:
    fone = lead["telefone_e164"]
    linhas = [
        f"Lead quente — {lead['nome']}",
        f"{lead['veiculo']}",
        qualificacao.resumo,
    ]
    if qualificacao.paga_hoje:
        linhas.append(f"Paga hoje: {qualificacao.paga_hoje}")
    if qualificacao.carro_quitado != "nao_informado":
        linhas.append(f"Carro: {qualificacao.carro_quitado}")
    linhas.append(f"Conversa: wa.me/{fone}")

    evo.enviar_texto(telefone_vendedora, "\n".join(linhas))
    eventos.registrar(
        conn, "quente",
        f"{lead['nome']} marcado como quente — aviso enviado à vendedora",
        agora, lead["id"],
    )
