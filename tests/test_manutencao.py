# tests/test_manutencao.py
from datetime import datetime, timedelta

from disparo.manutencao import backup, encerrar_sem_resposta
from disparo.maquina import Status, status_de

AGORA = datetime(2026, 8, 8, 10, 0)


def _contatar(conn, lead_id, quando):
    conn.execute(
        "UPDATE leads SET status = 'contatado', contatado_em = ? WHERE id = ?",
        (quando.isoformat(), lead_id),
    )
    conn.commit()


def test_encerra_apos_72h(conn, lead):
    _contatar(conn, lead, AGORA - timedelta(hours=73))
    assert encerrar_sem_resposta(conn, AGORA) == 1
    assert status_de(conn, lead) == Status.SEM_RESPOSTA


def test_nao_encerra_antes_de_72h(conn, lead):
    _contatar(conn, lead, AGORA - timedelta(hours=71))
    assert encerrar_sem_resposta(conn, AGORA) == 0
    assert status_de(conn, lead) == Status.CONTATADO


def test_nao_encerra_quem_ja_respondeu(conn, lead):
    _contatar(conn, lead, AGORA - timedelta(hours=100))
    conn.execute("UPDATE leads SET status = 'em_conversa' WHERE id = ?", (lead,))
    conn.commit()
    assert encerrar_sem_resposta(conn, AGORA) == 0


def test_backup_gera_arquivo_legivel(conn, lead, tmp_path):
    caminho = backup(conn, tmp_path, AGORA)
    assert caminho.exists()
    import sqlite3
    copia = sqlite3.connect(caminho)
    total = copia.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    assert total == 1
