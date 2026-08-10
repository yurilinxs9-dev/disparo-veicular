# tests/test_telefone.py
import pytest

from disparo.telefone import normalizar, variantes


@pytest.mark.parametrize("bruto,esperado", [
    ("11988884444", "5511988884444"),
    ("(11) 98888-4444", "5511988884444"),
    ("+55 11 98888-4444", "5511988884444"),
    ("5511988884444", "5511988884444"),
    ("11 3333-4444", "551133334444"),
    ("5532211234", "555532211234"),
    ("55988887766", "5555988887766"),
    ("555532211234", "555532211234"),
    ("5555988887766", "5555988887766"),
])
def test_normaliza_formatos_validos(bruto, esperado):
    assert normalizar(bruto) == esperado


@pytest.mark.parametrize("bruto", ["", "123", "abcdef", "5511", "1" * 20, None, "05987654321"])
def test_rejeita_invalidos(bruto):
    assert normalizar(bruto) is None


def test_variantes_com_nono_digito():
    assert set(variantes("5537991048239")) == {"5537991048239", "553791048239"}


def test_variantes_sem_nono_digito():
    assert set(variantes("553791048239")) == {"553791048239", "5537991048239"}


def test_variantes_formato_estranho_devolve_o_proprio():
    assert variantes("123") == ("123",)
