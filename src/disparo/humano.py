# src/disparo/humano.py
from __future__ import annotations

import random
import re

_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")


def primeiro_nome(nome: str) -> str:
    """Devolve o primeiro nome de `nome`, ou 'cliente' se vier vazio."""
    partes = nome.split()
    return partes[0] if partes else "cliente"


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
    """Divide uma resposta longa em mensagens curtas, cortando entre frases.

    Uma frase que sozinha já ultrapasse o limite (por exemplo, um trecho
    sem pontuação de fim) é dividida por palavra; uma palavra isolada que
    ainda assim ultrapasse o limite é fatiada por tamanho, como último
    recurso, para garantir que nenhuma parte devolvida exceda `limite`.
    """
    texto = texto.strip()
    if len(texto) <= limite:
        return [texto]

    partes: list[str] = []
    atual = ""
    for frase in _FIM_DE_FRASE.split(texto):
        candidato = f"{atual} {frase}".strip() if atual else frase
        if atual and len(candidato) > limite:
            partes.extend(_encurtar(atual, limite))
            atual = frase
        else:
            atual = candidato
    if atual:
        partes.extend(_encurtar(atual, limite))
    return partes


def _encurtar(fragmento: str, limite: int) -> list[str]:
    """Devolve `fragmento` como uma única parte se couber no limite;
    caso contrário, divide por palavra."""
    if len(fragmento) <= limite:
        return [fragmento]
    return _dividir_por_palavras(fragmento, limite)


def _dividir_por_palavras(fragmento: str, limite: int) -> list[str]:
    """Divide um fragmento (sem quebra de frase) em palavras, agrupando até
    o limite. Uma palavra isolada que ainda ultrapasse o limite é fatiada
    por tamanho, pois não há como reduzi-la mantendo a palavra inteira."""
    partes: list[str] = []
    atual = ""
    for palavra in fragmento.split():
        candidato = f"{atual} {palavra}".strip() if atual else palavra
        if atual and len(candidato) > limite:
            partes.append(atual)
            atual = ""
            candidato = palavra
        if len(candidato) > limite:
            partes.extend(_dividir_por_tamanho(candidato, limite))
            atual = ""
        else:
            atual = candidato
    if atual:
        partes.append(atual)
    return partes


def _dividir_por_tamanho(fragmento: str, limite: int) -> list[str]:
    """Último recurso: fatia uma palavra maior que o limite em pedaços de
    até `limite` caracteres, sem perder nem duplicar nenhum caractere."""
    return [fragmento[i : i + limite] for i in range(0, len(fragmento), limite)]
