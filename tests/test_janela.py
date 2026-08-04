# tests/test_janela.py
from datetime import datetime

from disparo.janela import dentro, proxima_abertura


def test_dentro_do_horario_util():
    assert dentro(datetime(2026, 8, 4, 10, 0)) is True   # terça 10h


def test_antes_da_abertura():
    assert dentro(datetime(2026, 8, 4, 8, 59)) is False


def test_depois_do_fechamento():
    assert dentro(datetime(2026, 8, 4, 18, 1)) is False


def test_borda_exata_abertura_e_fechamento():
    assert dentro(datetime(2026, 8, 4, 9, 0)) is True
    assert dentro(datetime(2026, 8, 4, 18, 0)) is True


def test_sabado_e_domingo_fora():
    assert dentro(datetime(2026, 8, 8, 10, 0)) is False   # sábado
    assert dentro(datetime(2026, 8, 9, 10, 0)) is False   # domingo


def test_proxima_abertura_no_mesmo_dia():
    assert proxima_abertura(datetime(2026, 8, 4, 7, 0)) == datetime(2026, 8, 4, 9, 0)


def test_proxima_abertura_pula_o_fim_de_semana():
    # sexta 19h -> segunda 9h
    assert proxima_abertura(datetime(2026, 8, 7, 19, 0)) == datetime(2026, 8, 10, 9, 0)
