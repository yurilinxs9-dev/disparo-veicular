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
from disparo.fila import FilaPorLead
from disparo.maquina import Status, robo_pode_falar, status_de, transicionar
from disparo.midia import MensagemNormalizada
from disparo.telefone import variantes

_DECISAO_PARA_STATUS = {
    "frio": Status.FRIO,
    "opt_out": Status.OPT_OUT,
    "dado_desatualizado": Status.DADO_DESATUALIZADO,
    "escalar": Status.ESCALADO,
}

_FASES_DE_FECHAMENTO = (Status.NEGOCIANDO, Status.AGUARDANDO_PAGAMENTO)

_MAX_REGENERACOES = 2


def _lead_por_telefone(conn: sqlite3.Connection, telefone: str) -> sqlite3.Row | None:
    candidatos = variantes(telefone)
    marcadores = ",".join("?" * len(candidatos))
    return conn.execute(
        f"SELECT * FROM leads WHERE telefone_e164 IN ({marcadores})", candidatos
    ).fetchone()


def _lead_por_id(conn: sqlite3.Connection, lead_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def _historico(conn: sqlite3.Connection, lead_id: int) -> list[dict]:
    linhas = conn.execute(
        "SELECT direcao, texto FROM mensagens WHERE lead_id = ? ORDER BY id",
        (lead_id,),
    ).fetchall()
    return [dict(linha) for linha in linhas]


def _garantir_turno_final_de_usuario(historico: list[dict]) -> list[dict]:
    """A API não aceita a lista de `messages` terminando em turno do
    assistente (ativa "prefill", incompatível com saída estruturada). Uma
    entrada pode ficar presa antes do bloco de saída da rodada anterior
    quando chega durante o envio dela (corrida do C1) — nesse caso repete a
    última entrada como turno final, garantindo que a conversa sempre
    termine com o lead falando."""
    if not historico or historico[-1]["direcao"] != "saida":
        return historico
    ultima_entrada = next(
        (m for m in reversed(historico) if m["direcao"] == "entrada"), None)
    if ultima_entrada is None:
        return historico
    return historico + [dict(ultima_entrada)]


def _historico_com_midia(conn: sqlite3.Connection, lead_id: int, midia) -> list[dict]:
    historico = _garantir_turno_final_de_usuario(_historico(conn, lead_id))
    if midia and historico and historico[-1]["direcao"] == "entrada":
        historico[-1]["imagem_b64"], historico[-1]["media_type"] = midia
    return historico


def _registrar_entrada(conn: sqlite3.Connection, lead_id: int,
                        mensagem: MensagemNormalizada, agora: datetime) -> bool:
    """Grava a mensagem recebida. Devolve False se era duplicada."""
    cursor = conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, transcricao, "
        "wa_message_id, criado_em) VALUES (?, 'entrada', ?, ?, ?, ?, ?) "
        "ON CONFLICT(wa_message_id) DO NOTHING",
        (lead_id, mensagem.tipo, mensagem.texto,
         mensagem.texto if mensagem.tipo == "audio" else None,
         mensagem.wa_message_id, agora.isoformat()),
    )
    conn.commit()
    return cursor.rowcount > 0


def processar(conn: sqlite3.Connection, evo, cliente_claude, cfg,
              mensagem: MensagemNormalizada, agora: datetime,
              rng: random.Random,
              dormir: Callable[[float], None] = _time.sleep,
              powercrm=None, *, fila: FilaPorLead | None = None,
              agora_envio: Callable[[], datetime] = datetime.now) -> None:
    """Trata uma mensagem recebida: grava, espera a janela e responde o bloco."""
    fila = fila if fila is not None else FilaPorLead()
    lead = _lead_por_telefone(conn, mensagem.telefone)
    if lead is None:
        return
    if not robo_pode_falar(status_de(conn, lead["id"])):
        return

    gravou = _registrar_entrada(conn, lead["id"], mensagem, agora)
    if not gravou:
        # webhook repetido: uma pendência órfã (se existir) é coberta pelo
        # laço assumir/liberar da rodada que a criou.
        return

    if mensagem.transcricao_falhou:
        eventos.registrar(
            conn, "alerta",
            f"Nao consegui transcrever o audio de {lead['nome']}",
            agora, lead["id"],
        )

    seq = fila.chegou(lead["id"], mensagem.imagem_b64, mensagem.media_type)
    if not fila.aguardar(lead["id"], seq, humano.janela_debounce(rng), dormir):
        return  # chegou mensagem mais nova; a rodada dela responde o bloco

    seq_coberto = seq
    while True:
        if not fila.assumir(lead["id"]):
            return  # rodada ativa reprocessa ao liberar
        try:
            seq_coberto = _responder(conn, evo, cliente_claude, cfg, lead, agora,
                                     rng, dormir, powercrm, fila, agora_envio,
                                     seq_coberto)
        except Exception as erro:  # não deixa a pendência morrer com a rodada
            eventos.registrar(
                conn, "alerta",
                f"Erro ao responder {lead['nome']}: {erro}",
                agora, lead["id"],
            )
        finally:
            reprocessar = fila.liberar(lead["id"])
        if not reprocessar:
            return


def _responder(conn: sqlite3.Connection, evo, cliente_claude, cfg, lead,
                agora: datetime, rng: random.Random,
                dormir: Callable[[float], None], powercrm, fila: FilaPorLead,
                agora_envio: Callable[[], datetime], seq_coberto: int) -> int:
    """Processa uma rodada. Devolve o seq que ficou coberto pela resposta
    (ou o seq recebido, sem mudança, se a rodada não respondeu nada)."""
    # tira a mídia pendente sempre, mesmo que a rodada aborte logo em
    # seguida — senão ela vaza da fila e gruda numa mensagem futura
    midia = fila.tirar_midia(lead["id"])

    if disjuntor.esta_pausado(conn):
        return seq_coberto  # kill switch: mensagem gravada, robô mudo

    lead = _lead_por_id(conn, lead["id"])  # turnos/status frescos a cada rodada
    if lead is None:
        return seq_coberto
    if not robo_pode_falar(status_de(conn, lead["id"])):
        return seq_coberto  # o lead pode ter saído do estado que permite falar
                            # enquanto esta rodada esperava a trava

    historico = _historico(conn, lead["id"])
    if not historico:
        return seq_coberto
    if historico[-1]["direcao"] == "saida" and not fila.mudou(lead["id"], seq_coberto):
        return seq_coberto  # nada além do que já foi coberto pela última resposta

    if status_de(conn, lead["id"]) == Status.CONTATADO:
        transicionar(conn, lead["id"], Status.EM_CONVERSA, agora)

    status_atual = status_de(conn, lead["id"])
    modelo = (cfg.modelo_fechamento if status_atual in _FASES_DE_FECHAMENTO
              else cfg.modelo_triagem)
    ferramentas = (Ferramentas(conn, powercrm, lead["id"], agora)
                   if powercrm is not None else None)

    dormir(humano.atraso_leitura(rng))
    ultima = conn.execute(
        "SELECT wa_message_id FROM mensagens WHERE lead_id = ? AND "
        "direcao = 'entrada' ORDER BY id DESC LIMIT 1", (lead["id"],)
    ).fetchone()
    if ultima and ultima["wa_message_id"]:
        evo.marcar_lida(lead["telefone_e164"], ultima["wa_message_id"])

    for _ in range(_MAX_REGENERACOES + 1):
        seq = fila.seq_atual(lead["id"])
        chamadas_antes = ferramentas.chamadas if ferramentas is not None else 0
        historico = _historico_com_midia(conn, lead["id"], midia)
        qualificacao = conversar(cliente_claude, dict(lead), historico,
                                 ferramentas=ferramentas, modelo=modelo)
        usou_ferramenta = (ferramentas is not None
                           and ferramentas.chamadas > chamadas_antes)
        if usou_ferramenta or not fila.mudou(lead["id"], seq):
            break  # já causou efeito externo, ou nada novo chegou: resposta vale

    turnos = lead["turnos"] + 1
    decisao = qualificacao.decisao
    if turnos >= TETO_TURNOS and decisao == "continuar":
        decisao = "escalar"
    if decisao != "opt_out":  # blocklist sempre vence sobre escaladas automáticas
        if ferramentas is not None and ferramentas.falhas_powercrm >= 2:
            decisao = "escalar"
        if ferramentas is not None and ferramentas.escalou:
            decisao = "escalar"

    dormir(humano.atraso_resposta(rng))
    if qualificacao.resposta:
        for parte in humano.quebrar(qualificacao.resposta):
            evo.digitando(lead["telefone_e164"],
                          humano.duracao_digitando(parte, rng))
            dormir(humano.duracao_digitando(parte, rng))
            wa_id = evo.enviar_texto(lead["telefone_e164"], parte)
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, "
                "wa_message_id, criado_em) VALUES (?, 'saida', 'texto', ?, ?, ?)",
                (lead["id"], parte, wa_id, agora_envio().isoformat()),
            )

    conn.execute(
        "UPDATE leads SET turnos = ?, resumo = ?, paga_hoje = ?, "
        "tem_cobertura = ?, carro_quitado = ?, ultimo_evento_em = ? "
        "WHERE id = ?",
        (turnos, qualificacao.resumo, qualificacao.paga_hoje,
         qualificacao.tem_cobertura, qualificacao.carro_quitado,
         agora.isoformat(), lead["id"]),
    )
    conn.commit()

    if ferramentas is not None and ferramentas.fechou:
        handoff.avisar_fechamento(
            conn, evo, cfg.equipe_telefone,
            _lead_por_id(conn, lead["id"]), agora,
        )

    novo_status = _DECISAO_PARA_STATUS.get(decisao)
    if novo_status is None:
        eventos.registrar(
            conn, "resposta", f"{lead['nome']} respondeu", agora, lead["id"]
        )
        return seq

    if status_de(conn, lead["id"]) != novo_status:
        transicionar(conn, lead["id"], novo_status, agora)

    if novo_status is Status.OPT_OUT:
        blocklist.bloquear(conn, lead["telefone_e164"], "opt_out", agora)
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
    return seq
