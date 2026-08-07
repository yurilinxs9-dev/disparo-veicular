from datetime import datetime

from disparo.disjuntor import avaliar, esta_pausado, pausar, retomar
from disparo.eventos import listar, registrar

AGORA = datetime(2026, 8, 4, 12, 0)


def _semear(conn, contatados: int, responderam: int, opt_outs: int):
    for i in range(contatados):
        conn.execute(
            "INSERT INTO leads (nome, telefone_e164, veiculo, status, criado_em, "
            "contatado_em) VALUES (?, ?, '', ?, ?, ?)",
            (f"L{i}", f"55119{i:08d}",
             "opt_out" if i < opt_outs else
             ("em_conversa" if i < responderam else "contatado"),
             AGORA.isoformat(), AGORA.isoformat()),
        )
    conn.commit()


def test_tudo_bem(conn):
    _semear(conn, contatados=50, responderam=15, opt_outs=0)
    assert avaliar(conn).ok is True


def test_resposta_baixa_dispara(conn):
    _semear(conn, contatados=50, responderam=4, opt_outs=0)
    v = avaliar(conn)
    assert v.ok is False
    assert "resposta" in v.motivo


def test_muitos_opt_outs_disparam(conn):
    _semear(conn, contatados=50, responderam=20, opt_outs=3)
    v = avaliar(conn)
    assert v.ok is False
    assert "opt-out" in v.motivo


def test_poucos_contatos_nao_disparam(conn):
    _semear(conn, contatados=5, responderam=0, opt_outs=0)
    assert avaliar(conn).ok is True


def test_pausar_e_retomar(conn):
    assert esta_pausado(conn) is False
    pausar(conn, "teste", AGORA)
    assert esta_pausado(conn) is True
    retomar(conn, AGORA)
    assert esta_pausado(conn) is False


def test_pausar_registra_evento(conn):
    pausar(conn, "taxa de resposta baixa", AGORA)
    eventos = listar(conn)
    assert eventos[0]["tipo"] == "alerta"
    assert "taxa de resposta baixa" in eventos[0]["texto"]


def test_listar_devolve_mais_recente_primeiro(conn):
    registrar(conn, "sistema", "primeiro", datetime(2026, 8, 4, 9, 0))
    registrar(conn, "sistema", "segundo", datetime(2026, 8, 4, 10, 0))
    assert listar(conn)[0]["texto"] == "segundo"


def test_opt_outs_disparam_abaixo_do_minimo(conn):
    _semear(conn, contatados=5, responderam=3, opt_outs=3)
    v = avaliar(conn)
    assert v.ok is False
    assert "opt-out" in v.motivo


def test_poucos_opt_outs_abaixo_do_minimo_nao_dispara(conn):
    _semear(conn, contatados=5, responderam=2, opt_outs=2)
    assert avaliar(conn).ok is True


def test_opt_outs_disparam_com_dezenove_contatos(conn):
    _semear(conn, contatados=19, responderam=3, opt_outs=3)
    v = avaliar(conn)
    assert v.ok is False
    assert "opt-out" in v.motivo


def _semear_status(conn, contatados: int, status_resposta: str, responderam: int):
    for i in range(contatados):
        conn.execute(
            "INSERT INTO leads (nome, telefone_e164, veiculo, status, criado_em, "
            "contatado_em) VALUES (?, ?, '', ?, ?, ?)",
            (f"S{i}", f"55115{i:08d}",
             status_resposta if i < responderam else "contatado",
             AGORA.isoformat(), AGORA.isoformat()),
        )
    conn.commit()


def test_pago_conta_como_resposta(conn):
    _semear_status(conn, contatados=20, status_resposta="pago", responderam=4)
    assert avaliar(conn).ok is True


def test_negociando_aguardando_e_escalado_contam_como_resposta(conn):
    for status in ("negociando", "aguardando_pagamento", "escalado"):
        conn.execute("DELETE FROM leads")
        conn.commit()
        _semear_status(conn, contatados=20, status_resposta=status, responderam=4)
        assert avaliar(conn).ok is True, status


def test_janela_usa_contatado_em_nao_id(conn):
    # Leads "novos" (contatado_em maior, sem opt-out) sao inseridos primeiro,
    # entao ficam com id MENOR. Leads "antigos" (contatado_em menor, com
    # opt-out) sao inseridos depois, entao ficam com id MAIOR. Se a consulta
    # ordenasse por id DESC em vez de contatado_em DESC, os opt-outs antigos
    # apareceriam na amostra de tamanho 5 e dispararia o disjuntor.
    for i in range(5):
        conn.execute(
            "INSERT INTO leads (nome, telefone_e164, veiculo, status, criado_em, "
            "contatado_em) VALUES (?, ?, '', 'contatado', ?, ?)",
            (f"N{i}", f"55118{i:08d}", AGORA.isoformat(),
             datetime(2026, 8, 4, 12, 0).isoformat()),
        )
    for i in range(5):
        conn.execute(
            "INSERT INTO leads (nome, telefone_e164, veiculo, status, criado_em, "
            "contatado_em) VALUES (?, ?, '', 'opt_out', ?, ?)",
            (f"O{i}", f"55117{i:08d}", AGORA.isoformat(),
             datetime(2026, 8, 1, 12, 0).isoformat()),
        )
    conn.commit()
    v = avaliar(conn, amostra=5)
    assert v.ok is True
