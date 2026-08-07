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


def _com_boleto(conn, lead, enviado_em):
    from disparo.maquina import Status, transicionar
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.NEGOCIANDO, AGORA)
    transicionar(conn, lead, Status.AGUARDANDO_PAGAMENTO, AGORA)
    conn.execute(
        "UPDATE leads SET cobranca_id='B1', boleto_url='https://p/b1', "
        "cobranca_enviada_em=? WHERE id=?",
        (enviado_em.isoformat(), lead))
    conn.commit()


class EvoFalsa:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return "WA"


def test_lembrete_unico_apos_48h(conn, lead):
    from disparo.manutencao import cobrar_pendentes
    _com_boleto(conn, lead, AGORA - timedelta(hours=49))
    evo = EvoFalsa()
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (1, 0)
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (0, 0)
    assert len(evo.enviados) == 1
    assert "boleto" in evo.enviados[0][1].lower()


def test_antes_de_48h_nada(conn, lead):
    from disparo.manutencao import cobrar_pendentes
    _com_boleto(conn, lead, AGORA - timedelta(hours=47))
    assert cobrar_pendentes(conn, EvoFalsa(), "x", AGORA) == (0, 0)


def test_pausado_nao_cobra_pendentes(conn, lead):
    from disparo.disjuntor import pausar
    from disparo.manutencao import cobrar_pendentes
    _com_boleto(conn, lead, AGORA - timedelta(hours=49))
    pausar(conn, "teste", AGORA)
    evo = EvoFalsa()
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (0, 0)
    assert evo.enviados == []


def test_72h_escala(conn, lead):
    from disparo.manutencao import cobrar_pendentes
    from disparo.maquina import Status, status_de
    _com_boleto(conn, lead, AGORA - timedelta(hours=73))
    evo = EvoFalsa()
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (0, 1)
    assert status_de(conn, lead) == Status.ESCALADO
    assert any(d == "5537999990000" for d, _ in evo.enviados)
