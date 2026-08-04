# src/disparo/importador.py
from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from disparo.blocklist import esta_bloqueado
from disparo.telefone import normalizar


@dataclass(frozen=True)
class RelatorioImportacao:
    lidos: int = 0
    importados: int = 0
    duplicados: int = 0
    invalidos: int = 0
    bloqueados: int = 0


def _coluna(linha: dict[str, str], *nomes: str) -> str:
    for chave, valor in linha.items():
        if chave is None:
            continue
        if chave.strip().lower() in nomes:
            return (valor or "").strip()
    return ""


def importar_csv(conn: sqlite3.Connection, texto: str,
                 agora: datetime) -> RelatorioImportacao:
    leitor = csv.DictReader(io.StringIO(texto))
    lidos = importados = duplicados = invalidos = bloqueados = 0
    vistos: set[str] = set()

    for linha in leitor:
        lidos += 1
        fone = normalizar(_coluna(linha, "telefone", "fone", "celular"))
        if fone is None:
            invalidos += 1
            continue
        if fone in vistos:
            duplicados += 1
            continue
        vistos.add(fone)
        if esta_bloqueado(conn, fone):
            bloqueados += 1
            continue
        cursor = conn.execute(
            "INSERT INTO leads (nome, telefone_e164, veiculo, criado_em) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(telefone_e164) DO NOTHING",
            (
                _coluna(linha, "nome") or "sem nome",
                fone,
                _coluna(linha, "veiculo", "carro", "modelo"),
                agora.isoformat(),
            ),
        )
        if cursor.rowcount == 1:
            importados += 1
        else:
            duplicados += 1

    conn.commit()
    return RelatorioImportacao(lidos, importados, duplicados, invalidos, bloqueados)
