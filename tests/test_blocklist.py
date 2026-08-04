from datetime import datetime

from disparo.blocklist import bloquear, esta_bloqueado

AGORA = datetime(2026, 8, 4, 10, 0)


def test_numero_bloqueado_e_reconhecido(conn):
    bloquear(conn, "5511988884444", "opt_out", AGORA)
    assert esta_bloqueado(conn, "5511988884444") is True


def test_numero_livre_nao_e_bloqueado(conn):
    assert esta_bloqueado(conn, "5511977773333") is False


def test_bloquear_duas_vezes_nao_explode(conn):
    bloquear(conn, "5511988884444", "opt_out", AGORA)
    bloquear(conn, "5511988884444", "opt_out de novo", AGORA)
    total = conn.execute("SELECT COUNT(*) c FROM blocklist").fetchone()["c"]
    assert total == 1
