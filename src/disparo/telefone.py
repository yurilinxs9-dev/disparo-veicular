# src/disparo/telefone.py
from __future__ import annotations

import re

_SO_DIGITOS = re.compile(r"\D+")


def normalizar(bruto: str | None) -> str | None:
    """Converte um telefone brasileiro para E.164 sem o sinal de mais.

    Aceita com ou sem DDI, com ou sem máscara. Devolve None se não for
    possível produzir um número brasileiro plausível.
    """
    if not bruto:
        return None
    digitos = _SO_DIGITOS.sub("", str(bruto))
    if digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) not in (10, 11):
        return None
    ddd = digitos[:2]
    if not ("11" <= ddd <= "99"):
        return None
    return "55" + digitos
