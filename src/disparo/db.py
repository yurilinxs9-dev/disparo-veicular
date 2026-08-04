from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    nome              TEXT NOT NULL,
    telefone_e164     TEXT NOT NULL UNIQUE,
    veiculo           TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'novo',
    etapa             INTEGER NOT NULL DEFAULT 0,
    resumo            TEXT NOT NULL DEFAULT '',
    tem_cobertura     TEXT NOT NULL DEFAULT 'nao_informado',
    paga_hoje         TEXT,
    carro_quitado     TEXT NOT NULL DEFAULT 'nao_informado',
    turnos            INTEGER NOT NULL DEFAULT 0,
    criado_em         TEXT NOT NULL,
    contatado_em      TEXT,
    ultimo_evento_em  TEXT
);

CREATE TABLE IF NOT EXISTS mensagens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER NOT NULL REFERENCES leads(id),
    direcao         TEXT NOT NULL CHECK (direcao IN ('entrada', 'saida')),
    tipo            TEXT NOT NULL DEFAULT 'texto',
    texto           TEXT NOT NULL DEFAULT '',
    transcricao     TEXT,
    wa_message_id   TEXT UNIQUE,
    criado_em       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocklist (
    telefone_e164 TEXT PRIMARY KEY,
    motivo        TEXT NOT NULL,
    criado_em     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS envios_diarios (
    dia        TEXT PRIMARY KEY,
    quantidade INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eventos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo      TEXT NOT NULL,
    lead_id   INTEGER REFERENCES leads(id),
    texto     TEXT NOT NULL,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_mensagens_lead ON mensagens(lead_id, id);
CREATE INDEX IF NOT EXISTS idx_eventos_criado ON eventos(criado_em DESC);
"""


def conectar(caminho: Path) -> sqlite3.Connection:
    caminho = Path(caminho)
    if caminho.parent != Path(""):
        caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def criar_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
