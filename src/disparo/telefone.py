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
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) not in (10, 11):
        return None
    ddd = digitos[:2]
    if not ("11" <= ddd <= "99"):
        return None
    return "55" + digitos


def variantes(telefone: str) -> tuple[str, ...]:
    """Variações com e sem o nono dígito — o JID do WhatsApp pode vir sem ele."""
    local = telefone[2:] if telefone.startswith("55") else telefone
    if len(local) == 11 and local[2] == "9":
        return ("55" + local, "55" + local[:2] + local[3:])
    if len(local) == 10:
        return ("55" + local, "55" + local[:2] + "9" + local[2:])
    return (telefone,)
