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


def avisar_escalada(conn: sqlite3.Connection, evo, telefone_equipe: str,
                    lead: sqlite3.Row, motivo: str, agora: datetime) -> None:
    fone = lead["telefone_e164"]
    texto = "\n".join([
        f"Assumir conversa — {lead['nome']}",
        f"{lead['veiculo']}",
        motivo or "a IA escalou a conversa",
        f"Conversa: wa.me/{fone}",
    ])
    evo.enviar_texto(telefone_equipe, texto)
    eventos.registrar(conn, "alerta",
                      f"{lead['nome']} escalado para a equipe",
                      agora, lead["id"])


def avisar_vistoria(conn: sqlite3.Connection, evo, telefone_equipe: str,
                    lead: sqlite3.Row, agora: datetime) -> None:
    fone = lead["telefone_e164"]
    texto = "\n".join([
        f"VENDA PAGA — {lead['nome']}",
        f"{lead['veiculo']} — placa {lead['placa'] or '?'}",
        f"Plano {lead['plano'] or '?'} · R$ {lead['mensalidade'] or '?'}/mês",
        "Agendar vistoria.",
        f"Conversa: wa.me/{fone}",
    ])
    evo.enviar_texto(telefone_equipe, texto)
    eventos.registrar(conn, "quente",
                      f"{lead['nome']} pagou — vistoria pendente",
                      agora, lead["id"])
