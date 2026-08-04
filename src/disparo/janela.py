# src/disparo/janela.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Janela:
    inicio: time
    fim: time
    dias: frozenset[int]  # 0 = segunda, 6 = domingo


PADRAO = Janela(time(9, 0), time(18, 0), frozenset({0, 1, 2, 3, 4}))


def dentro(agora: datetime, j: Janela = PADRAO) -> bool:
    if agora.weekday() not in j.dias:
        return False
    return j.inicio <= agora.time() <= j.fim


def proxima_abertura(agora: datetime, j: Janela = PADRAO) -> datetime:
    candidato = agora
    if candidato.time() > j.inicio or candidato.weekday() not in j.dias:
        if candidato.time() >= j.inicio:
            candidato = candidato + timedelta(days=1)
    candidato = candidato.replace(
        hour=j.inicio.hour, minute=j.inicio.minute, second=0, microsecond=0
    )
    while candidato.weekday() not in j.dias:
        candidato += timedelta(days=1)
    return candidato
