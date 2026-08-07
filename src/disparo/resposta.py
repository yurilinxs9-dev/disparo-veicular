# src/disparo/resposta.py
from __future__ import annotations

import random
import sqlite3
import time as _time
from datetime import datetime
from typing import Callable

from disparo import blocklist, disjuntor, eventos, handoff, humano
from disparo.conversador import TETO_TURNOS, conversar
from disparo.ferramentas import Ferramentas
from disparo.maquina import Status, robo_pode_falar, status_de, transicionar
from disparo.midia import MensagemNormalizada

_DECISAO_PARA_STATUS = {
    "frio": Status.FRIO,
    "opt_out": Status.OPT_OUT,
    "dado_desatualizado": Status.DADO_DESATUALIZADO,
    "escalar": Status.ESCALADO,
}

_FASES_DE_FECHAMENTO = (Status.NEGOCIANDO, Status.AGUARDANDO_PAGAMENTO)


def _lead_por_telefone(conn: sqlite3.Connection, telefone: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM leads WHERE telefone_e164 = ?", (telefone,)
    ).fetchone()


def _historico(conn: sqlite3.Connection, lead_id: int) -> list[dict]:
    linhas = conn.execute(
        "SELECT direcao, texto FROM mensagens WHERE lead_id = ? ORDER BY id",
        (lead_id,),
    ).fetchall()
    return [dict(linha) for linha in linhas]


def processar(conn: sqlite3.Connection, evo, cliente_claude, cfg,
              mensagem: MensagemNormalizada, agora: datetime,
              rng: random.Random,
              dormir: Callable[[float], None] = _time.sleep,
              powercrm=None) -> None:
    """Trata uma mensagem recebida: grava, responde e atualiza o estado do lead."""
    lead = _lead_por_telefone(conn, mensagem.telefone)
    if lead is None:
        return

    if not robo_pode_falar(status_de(conn, lead["id"])):
        return

    cursor = conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, transcricao, "
        "wa_message_id, criado_em) VALUES (?, 'entrada', ?, ?, ?, ?, ?) "
        "ON CONFLICT(wa_message_id) DO NOTHING",
        (lead["id"], mensagem.tipo, mensagem.texto,
         mensagem.texto if mensagem.tipo == "audio" else None,
         mensagem.wa_message_id, agora.isoformat()),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return  # webhook repetido

    if disjuntor.esta_pausado(conn):
        return  # kill switch: mensagem gravada, robô mudo

    if mensagem.transcricao_falhou:
        eventos.registrar(
            conn, "alerta",
            f"Nao consegui transcrever o audio de {lead['nome']}",
            agora, lead["id"],
        )

    if status_de(conn, lead["id"]) == Status.CONTATADO:
        transicionar(conn, lead["id"], Status.EM_CONVERSA, agora)

    status_atual = status_de(conn, lead["id"])
    modelo = (cfg.modelo_fechamento if status_atual in _FASES_DE_FECHAMENTO
              else cfg.modelo_triagem)
    ferramentas = (Ferramentas(conn, powercrm, lead["id"], agora)
                   if powercrm is not None else None)

    dormir(humano.atraso_leitura(rng))
    evo.marcar_lida(mensagem.telefone, mensagem.wa_message_id)

    historico = _historico(conn, lead["id"])
    if mensagem.imagem_b64:
        historico[-1]["imagem_b64"] = mensagem.imagem_b64
        historico[-1]["media_type"] = mensagem.media_type

    qualificacao = conversar(cliente_claude, dict(lead), historico,
                             ferramentas=ferramentas, modelo=modelo)

    turnos = lead["turnos"] + 1
    decisao = qualificacao.decisao
    if turnos >= TETO_TURNOS and decisao == "continuar":
        decisao = "escalar"
    if decisao != "opt_out":  # blocklist sempre vence sobre as escaladas automáticas
        if ferramentas is not None and ferramentas.falhas_powercrm >= 2:
            decisao = "escalar"
        if ferramentas is not None and ferramentas.escalou:
            decisao = "escalar"

    dormir(humano.atraso_resposta(rng))
    if qualificacao.resposta:
        for parte in humano.quebrar(qualificacao.resposta):
            evo.digitando(mensagem.telefone, humano.duracao_digitando(parte, rng))
            dormir(humano.duracao_digitando(parte, rng))
            wa_id = evo.enviar_texto(mensagem.telefone, parte)
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, wa_message_id, "
                "criado_em) VALUES (?, 'saida', 'texto', ?, ?, ?)",
                (lead["id"], parte, wa_id, agora.isoformat()),
            )

    conn.execute(
        "UPDATE leads SET turnos = ?, resumo = ?, paga_hoje = ?, tem_cobertura = ?, "
        "carro_quitado = ?, ultimo_evento_em = ? WHERE id = ?",
        (turnos, qualificacao.resumo, qualificacao.paga_hoje,
         qualificacao.tem_cobertura, qualificacao.carro_quitado,
         agora.isoformat(), lead["id"]),
    )
    conn.commit()

    novo_status = _DECISAO_PARA_STATUS.get(decisao)
    if novo_status is None:
        eventos.registrar(
            conn, "resposta", f"{lead['nome']} respondeu", agora, lead["id"]
        )
        return

    if status_de(conn, lead["id"]) != novo_status:
        transicionar(conn, lead["id"], novo_status, agora)

    if novo_status is Status.OPT_OUT:
        blocklist.bloquear(conn, mensagem.telefone, "opt_out", agora)
        eventos.registrar(
            conn, "alerta",
            f"{lead['nome']} pediu opt-out — número na blocklist",
            agora, lead["id"],
        )
    elif novo_status is Status.ESCALADO:
        handoff.avisar_escalada(
            conn, evo, cfg.equipe_telefone, lead, qualificacao.resumo, agora
        )
    else:
        eventos.registrar(
            conn, "sistema", f"{lead['nome']} encerrado como {novo_status}",
            agora, lead["id"],
        )
