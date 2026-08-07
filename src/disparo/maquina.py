from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum


class Status(StrEnum):
    NOVO = "novo"
    CONTATADO = "contatado"
    EM_CONVERSA = "em_conversa"
    NEGOCIANDO = "negociando"
    AGUARDANDO_PAGAMENTO = "aguardando_pagamento"
    PAGO = "pago"
    ESCALADO = "escalado"
    QUENTE = "quente"
    FRIO = "frio"
    OPT_OUT = "opt_out"
    DADO_DESATUALIZADO = "dado_desatualizado"
    SEM_RESPOSTA = "sem_resposta"
    INVALIDO = "invalido"


TERMINAIS = frozenset({
    Status.PAGO, Status.ESCALADO, Status.QUENTE, Status.FRIO, Status.OPT_OUT,
    Status.DADO_DESATUALIZADO, Status.SEM_RESPOSTA, Status.INVALIDO,
})

_FECHAMENTOS = frozenset({
    Status.ESCALADO, Status.QUENTE, Status.FRIO, Status.OPT_OUT,
    Status.DADO_DESATUALIZADO,
})

TRANSICOES: dict[Status, frozenset[Status]] = {
    Status.NOVO: frozenset({Status.CONTATADO, Status.INVALIDO}),
    Status.CONTATADO: frozenset({Status.EM_CONVERSA, Status.SEM_RESPOSTA} | _FECHAMENTOS),
    Status.EM_CONVERSA: frozenset({Status.NEGOCIANDO, Status.SEM_RESPOSTA} | _FECHAMENTOS),
    Status.NEGOCIANDO: frozenset({Status.AGUARDANDO_PAGAMENTO, Status.SEM_RESPOSTA} | _FECHAMENTOS),
    Status.AGUARDANDO_PAGAMENTO: frozenset({Status.PAGO, Status.SEM_RESPOSTA} | _FECHAMENTOS),
}


class TransicaoInvalida(RuntimeError):
    pass


def status_de(conn: sqlite3.Connection, lead_id: int) -> Status:
    linha = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if linha is None:
        raise TransicaoInvalida(f"lead {lead_id} não existe")
    return Status(linha["status"])


def transicionar(conn: sqlite3.Connection, lead_id: int, para: Status,
                 agora: datetime) -> None:
    atual = status_de(conn, lead_id)
    permitidos = TRANSICOES.get(atual, frozenset())
    if para not in permitidos:
        raise TransicaoInvalida(f"{atual} -> {para} não é permitido")
    conn.execute(
        "UPDATE leads SET status = ?, ultimo_evento_em = ? WHERE id = ?",
        (para.value, agora.isoformat(), lead_id),
    )
    conn.commit()


def robo_pode_falar(status: Status) -> bool:
    return status in (Status.CONTATADO, Status.EM_CONVERSA,
                      Status.NEGOCIANDO, Status.AGUARDANDO_PAGAMENTO)
