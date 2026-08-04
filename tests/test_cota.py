# tests/test_cota.py
from datetime import date

import pytest

from disparo.cota import (definir_inicio, enviados_no_dia, limite_do_dia,
                          registrar_envio, tem_cota)

INICIO = date(2026, 8, 3)


@pytest.mark.parametrize("dias_passados,esperado", [
    (0, 10), (1, 10), (2, 10),
    (3, 20), (6, 20),
    (7, 30), (30, 30),
])
def test_rampa(conn, dias_passados, esperado):
    definir_inicio(conn, INICIO)
    hoje = date.fromordinal(INICIO.toordinal() + dias_passados)
    assert limite_do_dia(conn, hoje) == esperado


def test_sem_inicio_definido_o_limite_e_zero(conn):
    assert limite_do_dia(conn, date(2026, 8, 4)) == 0


def test_contagem_por_dia_e_isolada(conn):
    definir_inicio(conn, INICIO)
    registrar_envio(conn, date(2026, 8, 4))
    registrar_envio(conn, date(2026, 8, 4))
    registrar_envio(conn, date(2026, 8, 5))
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 2
    assert enviados_no_dia(conn, date(2026, 8, 5)) == 1


def test_tem_cota_ate_o_limite(conn):
    definir_inicio(conn, INICIO)
    hoje = INICIO  # limite 10
    for _ in range(10):
        assert tem_cota(conn, hoje) is True
        registrar_envio(conn, hoje)
    assert tem_cota(conn, hoje) is False
