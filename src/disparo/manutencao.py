# src/disparo/manutencao.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from disparo import eventos
from disparo.maquina import Status, transicionar


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


def backup(conn: sqlite3.Connection, destino: Path, agora: datetime) -> Path:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"disparo-{agora:%Y%m%d}.db"
    copia = sqlite3.connect(caminho)
    with copia:
        conn.backup(copia)
    copia.close()
    return caminho
