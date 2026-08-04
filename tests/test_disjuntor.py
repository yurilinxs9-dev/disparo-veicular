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
