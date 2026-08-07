# src/disparo/agendador.py
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from disparo import cota, disjuntor, eventos, janela
from disparo.conversador import abertura
from disparo.evolution import EvolutionErro
from disparo.humano import primeiro_nome
from disparo.maquina import Status, transicionar


@dataclass(frozen=True)
class Resultado:
    enviou: bool
    motivo: str


def _proximo_lead(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, nome, telefone_e164, veiculo FROM leads "
        "WHERE status = 'novo' ORDER BY id LIMIT 1"
    ).fetchone()


def tentar_disparar(conn: sqlite3.Connection, evo, agora: datetime,
                    rng: random.Random) -> Resultado:
    """Envia no máximo uma mensagem de abertura. Chamado a cada minuto."""
    if disjuntor.esta_pausado(conn):
        return Resultado(False, "pausado")

    if not janela.dentro(agora):
        return Resultado(False, "fora da janela")

    veredito = disjuntor.avaliar(conn)
    if not veredito.ok:
        disjuntor.pausar(conn, veredito.motivo or "sinal ruim", agora)
        return Resultado(False, "disjuntor disparou")

    if not cota.tem_cota(conn, agora.date()):
        return Resultado(False, "cota do dia esgotada")

    lead = _proximo_lead(conn)
    if lead is None:
        return Resultado(False, "sem lead novo")

    try:
        if not evo.numero_existe(lead["telefone_e164"]):
            transicionar(conn, lead["id"], Status.INVALIDO, agora)
            eventos.registrar(
                conn, "sistema",
                f"{lead['telefone_e164']} não existe no WhatsApp — cota preservada",
                agora, lead["id"],
            )
            return Resultado(False, "numero inexistente")

        texto = abertura(primeiro_nome(lead["nome"]), rng)
        wa_id = evo.enviar_texto(lead["telefone_e164"], texto)
    except EvolutionErro as erro:
        eventos.registrar(conn, "alerta", f"Falha ao enviar: {erro}", agora, lead["id"])
        return Resultado(False, "evolution indisponivel")

    conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, wa_message_id, criado_em) "
        "VALUES (?, 'saida', 'texto', ?, ?, ?)",
        (lead["id"], texto, wa_id, agora.isoformat()),
    )
    conn.execute(
        "UPDATE leads SET contatado_em = ?, etapa = 1 WHERE id = ?",
        (agora.isoformat(), lead["id"]),
    )
    conn.commit()
    transicionar(conn, lead["id"], Status.CONTATADO, agora)
    cota.registrar_envio(conn, agora.date())
    eventos.registrar(
        conn, "envio",
        f"Disparo para {lead['nome']} — {lead['veiculo']}", agora, lead["id"],
    )
    return Resultado(True, "enviado")
