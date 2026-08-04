# tests/test_telefone.py
import pytest

from disparo.telefone import normalizar


@pytest.mark.parametrize("bruto,esperado", [
    ("11988884444", "5511988884444"),
    ("(11) 98888-4444", "5511988884444"),
    ("+55 11 98888-4444", "5511988884444"),
    ("5511988884444", "5511988884444"),
    ("11 3333-4444", "551133334444"),
])
def test_normaliza_formatos_validos(bruto, esperado):
    assert normalizar(bruto) == esperado


@pytest.mark.parametrize("bruto", ["", "123", "abcdef", "5511", "1" * 20, None])
def test_rejeita_invalidos(bruto):
    assert normalizar(bruto) is None
