# src/disparo/humano.py
from __future__ import annotations

import random
import re

_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")


def atraso_leitura(rng: random.Random) -> float:
    """Segundos entre a mensagem chegar e ser marcada como lida."""
    return rng.uniform(3, 20)


def atraso_resposta(rng: random.Random) -> float:
    """Segundos entre ler a mensagem do lead e começar a responder."""
    return rng.uniform(15, 180)


def intervalo_entre_disparos(rng: random.Random) -> float:
    """Segundos entre um disparo de abertura e o próximo."""
    return rng.uniform(120, 480)


def duracao_digitando(texto: str, rng: random.Random) -> float:
    """Segundos exibindo 'digitando…', proporcional ao tamanho da resposta."""
    base = min(len(texto) / 60, 6.0)
    return max(2.0, min(8.0, base + rng.uniform(0, 2)))


def quebrar(texto: str, limite: int = 160) -> list[str]:
    """Divide uma resposta longa em mensagens curtas, cortando entre frases."""
    texto = texto.strip()
    if len(texto) <= limite:
        return [texto]

    partes: list[str] = []
    atual = ""
    for frase in _FIM_DE_FRASE.split(texto):
        candidato = f"{atual} {frase}".strip() if atual else frase
        if atual and len(candidato) > limite:
            partes.append(atual)
            atual = frase
        else:
            atual = candidato
    if atual:
        partes.append(atual)
    return partes
