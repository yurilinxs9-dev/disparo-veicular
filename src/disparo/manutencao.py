# src/disparo/manutencao.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from disparo import disjuntor, eventos, handoff
from disparo.humano import primeiro_nome
from disparo.maquina import Status, transicionar

LEMBRETE = ("Oi {nome}, tudo bem? Só lembrando do boleto da proteção: {boleto}. "
            "Dá pra pagar pelo PIX no próprio boleto. Qualquer dúvida me chama.")


def encerrar_sem_resposta(conn: sqlite3.Connection, agora: datetime,
                          horas: int = 72) -> int:
    corte = (agora - timedelta(hours=horas)).isoformat()
    linhas = conn.execute(
        "SELECT id, nome FROM leads WHERE status = 'contatado' AND contatado_em < ?",
        (corte,),
    ).fetchall()
    for linha in linhas:
        transicionar(conn, linha["id"], Status.SEM_RESPOSTA, agora)
        eventos.registrar(
            conn, "sistema",
            f"{linha['nome']} encerrado sem resposta após {horas}h",
            agora, linha["id"],
        )
    return len(linhas)


def cobrar_pendentes(conn: sqlite3.Connection, evo, telefone_equipe: str,
                     agora: datetime) -> tuple[int, int]:
    if disjuntor.esta_pausado(conn):
        return (0, 0)  # kill switch: nem lembrete nem escalada enquanto pausado

    corte_48 = (agora - timedelta(hours=48)).isoformat()
    corte_72 = (agora - timedelta(hours=72)).isoformat()

    vencidos = conn.execute(
        "SELECT * FROM leads WHERE status = 'aguardando_pagamento' "
        "AND cobranca_enviada_em < ?", (corte_72,),
    ).fetchall()
    for lead in vencidos:
        transicionar(conn, lead["id"], Status.ESCALADO, agora)
        handoff.avisar_escalada(conn, evo, telefone_equipe, lead,
                                "boleto ha 72h sem pagamento", agora)

    pendentes = conn.execute(
        "SELECT * FROM leads WHERE status = 'aguardando_pagamento' "
        "AND cobranca_enviada_em < ? AND lembrete_em IS NULL", (corte_48,),
    ).fetchall()
    for lead in pendentes:
        evo.enviar_texto(lead["telefone_e164"], LEMBRETE.format(
            nome=primeiro_nome(lead["nome"]), boleto=lead["boleto_url"]))
        conn.execute("UPDATE leads SET lembrete_em = ? WHERE id = ?",
                     (agora.isoformat(), lead["id"]))
        conn.commit()
        eventos.registrar(conn, "sistema",
                          f"Lembrete de boleto para {lead['nome']}",
                          agora, lead["id"])

    return len(pendentes), len(vencidos)


def backup(conn: sqlite3.Connection, destino: Path, agora: datetime) -> Path:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"disparo-{agora:%Y%m%d}.db"
    copia = sqlite3.connect(caminho)
    with copia:
        conn.backup(copia)
    copia.close()
    return caminho
