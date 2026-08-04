# Disparo e Qualificação de Leads — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir um serviço Python que importa listas CSV, abre conversa no WhatsApp com até 30 pessoas por dia, qualifica cada lead com o Claude Haiku em tom natural, e entrega os leads quentes para a vendedora — com proteções contra bloqueio do número e um painel de monitoramento.

**Architecture:** Serviço único FastAPI rodando no VPS ao lado da Evolution API. Estado inteiro em SQLite. Um agendador APScheduler decide quando disparar; um webhook recebe as respostas; o Haiku escreve as respostas e classifica o lead. Módulos pequenos, cada um com uma responsabilidade e sem dependência circular: as regras de negócio (cota, janela, estados, disjuntor) não conhecem HTTP nem o Claude, e são testadas sem rede.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, APScheduler, anthropic SDK, faster-whisper, pydantic v2, sqlite3 (stdlib), pytest, respx.

## Global Constraints

- Todo texto de código, nomes de função e nomes de tabela em **português**, sem acento e sem cedilha nos identificadores (`nao_informado`, `veiculo`, `dado_desatualizado`).
- **Nunca usar `datetime.now()` dentro de regra de negócio.** Toda função que depende do tempo recebe `agora: datetime` como parâmetro. Só o agendador e as rotas HTTP leem o relógio.
- Todo acesso ao banco recebe uma `sqlite3.Connection` como primeiro parâmetro. Nenhum módulo abre conexão por conta própria, exceto `db.conectar`.
- Modelo do Claude: exatamente `claude-haiku-4-5`. Nunca outro.
- Segredos só por variável de ambiente. Nenhuma chave no código ou nos testes.
- Limite de envio: **30 por dia**, com rampa 10 / 20 / 30 (dias 1–3, 4–7, 8+).
- Janela padrão: **09:00–18:00, segunda a sexta**.
- Nenhum teste faz chamada de rede real. `httpx` é mockado com `respx`; o cliente do Claude é substituído por um duplo de teste.
- Cada tarefa termina com commit. Mensagens de commit em português, no formato `tipo: descrição`.

---

## Estrutura de arquivos

```
pyproject.toml
.env.example
src/disparo/
  config.py        Carrega variáveis de ambiente numa dataclass imutável
  db.py            Conexão e criação de schema
  telefone.py      Normalização para E.164
  blocklist.py     Consulta e inserção na blocklist
  importador.py    CSV -> leads
  cota.py          Rampa de volume e contagem diária
  janela.py        Horário comercial
  maquina.py       Estados do lead e transições válidas
  disjuntor.py     Pausa automática por sinal ruim
  eventos.py       Registro de eventos para o painel
  humano.py        Atrasos, duração de digitação, quebra de mensagem
  evolution.py     Cliente HTTP da Evolution API
  midia.py         Normaliza áudio/imagem/etc em texto para o modelo
  conversador.py   Prompt, schema e chamada ao Haiku
  handoff.py       Aviso à vendedora
  agendador.py     Loop de disparo
  webhook.py       Rota que recebe mensagens da Evolution
  painel.py        Rotas do painel, SSE e autenticação
  app.py           Monta o FastAPI e liga o agendador
  static/painel.html
tests/
  test_telefone.py test_blocklist.py test_importador.py test_cota.py
  test_janela.py test_maquina.py test_disjuntor.py test_humano.py
  test_evolution.py test_midia.py test_conversador.py test_agendador.py
  test_webhook.py test_handoff.py test_painel.py
  conftest.py
```

---

### Task 1: Esqueleto, configuração e banco

**Files:**
- Create: `pyproject.toml`, `.env.example`, `src/disparo/__init__.py`, `src/disparo/config.py`, `src/disparo/db.py`, `tests/conftest.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: nada.
- Produces: `config.Config` (dataclass), `config.carregar_config(env: Mapping[str, str]) -> Config`, `db.conectar(caminho: Path) -> sqlite3.Connection`, `db.criar_schema(conn: sqlite3.Connection) -> None`. A fixture `conn` do `conftest.py` é usada por todas as tarefas seguintes.

- [ ] **Step 1: Criar `pyproject.toml`**

```toml
[project]
name = "disparo-veicular"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "httpx>=0.27",
    "apscheduler>=3.10",
    "anthropic>=0.69",
    "pydantic>=2.9",
    "python-multipart>=0.0.12",
    "faster-whisper>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "respx>=0.21", "pytest-asyncio>=0.24"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/disparo"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Criar `.env.example`**

```bash
DISPARO_DB=./dados/disparo.db
ANTHROPIC_API_KEY=
EVOLUTION_BASE_URL=http://localhost:8080
EVOLUTION_API_KEY=
EVOLUTION_INSTANCE=portosul-01
VENDEDORA_TELEFONE=5511999999999
PAINEL_SENHA=
WHISPER_MODELO=small
```

- [ ] **Step 3: Escrever o teste que falha**

```python
# tests/test_db.py
from disparo.config import carregar_config
from disparo.db import conectar, criar_schema


def test_config_le_variaveis_de_ambiente():
    cfg = carregar_config({
        "DISPARO_DB": "/tmp/x.db",
        "ANTHROPIC_API_KEY": "sk-teste",
        "EVOLUTION_BASE_URL": "http://evo:8080",
        "EVOLUTION_API_KEY": "evo-key",
        "EVOLUTION_INSTANCE": "portosul-01",
        "VENDEDORA_TELEFONE": "5511999999999",
        "PAINEL_SENHA": "segredo",
    })
    assert cfg.evolution_instance == "portosul-01"
    assert cfg.whisper_modelo == "small"


def test_schema_cria_todas_as_tabelas(tmp_path):
    conn = conectar(tmp_path / "t.db")
    criar_schema(conn)
    nomes = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"leads", "mensagens", "blocklist", "envios_diarios",
            "eventos", "config"} <= nomes


def test_criar_schema_e_idempotente(tmp_path):
    conn = conectar(tmp_path / "t.db")
    criar_schema(conn)
    criar_schema(conn)  # não pode explodir
```

- [ ] **Step 4: Rodar e confirmar que falha**

Run: `pytest tests/test_db.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.config'`

- [ ] **Step 5: Implementar `config.py`**

```python
# src/disparo/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    db: Path
    anthropic_api_key: str
    evolution_base_url: str
    evolution_api_key: str
    evolution_instance: str
    vendedora_telefone: str
    painel_senha: str
    whisper_modelo: str


def carregar_config(env: Mapping[str, str] | None = None) -> Config:
    e = env if env is not None else os.environ
    faltando = [
        chave for chave in (
            "DISPARO_DB", "ANTHROPIC_API_KEY", "EVOLUTION_BASE_URL",
            "EVOLUTION_API_KEY", "EVOLUTION_INSTANCE",
            "VENDEDORA_TELEFONE", "PAINEL_SENHA",
        )
        if not e.get(chave)
    ]
    if faltando:
        raise RuntimeError("variáveis de ambiente ausentes: " + ", ".join(faltando))
    return Config(
        db=Path(e["DISPARO_DB"]),
        anthropic_api_key=e["ANTHROPIC_API_KEY"],
        evolution_base_url=e["EVOLUTION_BASE_URL"].rstrip("/"),
        evolution_api_key=e["EVOLUTION_API_KEY"],
        evolution_instance=e["EVOLUTION_INSTANCE"],
        vendedora_telefone=e["VENDEDORA_TELEFONE"],
        painel_senha=e["PAINEL_SENHA"],
        whisper_modelo=e.get("WHISPER_MODELO", "small"),
    )
```

- [ ] **Step 6: Implementar `db.py`**

```python
# src/disparo/db.py
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
```

- [ ] **Step 7: Criar `tests/conftest.py`**

```python
# tests/conftest.py
import sqlite3
from datetime import datetime

import pytest

from disparo.db import conectar, criar_schema


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = conectar(tmp_path / "teste.db")
    criar_schema(c)
    yield c
    c.close()


@pytest.fixture
def lead(conn) -> int:
    conn.execute(
        "INSERT INTO leads (nome, telefone_e164, veiculo, criado_em) "
        "VALUES (?, ?, ?, ?)",
        ("Joao", "5511988884444", "Onix 2019", datetime(2026, 8, 4, 9).isoformat()),
    )
    conn.commit()
    return conn.execute("SELECT id FROM leads").fetchone()["id"]
```

- [ ] **Step 8: Rodar e confirmar que passa**

Run: `pytest tests/test_db.py -v`
Expected: PASS (3 testes)

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .env.example src/disparo tests
git commit -m "feat: esqueleto do projeto, config e schema do banco"
```

---

### Task 2: Normalização de telefone

**Files:**
- Create: `src/disparo/telefone.py`, `tests/test_telefone.py`

**Interfaces:**
- Consumes: nada.
- Produces: `telefone.normalizar(bruto: str) -> str | None` — devolve E.164 sem `+` (ex.: `5511988884444`) ou `None` se o número for inválido.

- [ ] **Step 1: Escrever o teste que falha**

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_telefone.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.telefone'`

- [ ] **Step 3: Implementar**

```python
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_telefone.py -v`
Expected: PASS (11 casos)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/telefone.py tests/test_telefone.py
git commit -m "feat: normalizacao de telefone para E.164"
```

---

### Task 3: Blocklist

**Files:**
- Create: `src/disparo/blocklist.py`, `tests/test_blocklist.py`

**Interfaces:**
- Consumes: fixture `conn` (Task 1).
- Produces: `blocklist.bloquear(conn, telefone: str, motivo: str, agora: datetime) -> None`, `blocklist.esta_bloqueado(conn, telefone: str) -> bool`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_blocklist.py
from datetime import datetime

from disparo.blocklist import bloquear, esta_bloqueado

AGORA = datetime(2026, 8, 4, 10, 0)


def test_numero_bloqueado_e_reconhecido(conn):
    bloquear(conn, "5511988884444", "opt_out", AGORA)
    assert esta_bloqueado(conn, "5511988884444") is True


def test_numero_livre_nao_e_bloqueado(conn):
    assert esta_bloqueado(conn, "5511977773333") is False


def test_bloquear_duas_vezes_nao_explode(conn):
    bloquear(conn, "5511988884444", "opt_out", AGORA)
    bloquear(conn, "5511988884444", "opt_out de novo", AGORA)
    total = conn.execute("SELECT COUNT(*) c FROM blocklist").fetchone()["c"]
    assert total == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_blocklist.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.blocklist'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/blocklist.py
from __future__ import annotations

import sqlite3
from datetime import datetime


def bloquear(conn: sqlite3.Connection, telefone: str, motivo: str,
             agora: datetime) -> None:
    conn.execute(
        "INSERT INTO blocklist (telefone_e164, motivo, criado_em) VALUES (?, ?, ?) "
        "ON CONFLICT(telefone_e164) DO NOTHING",
        (telefone, motivo, agora.isoformat()),
    )
    conn.commit()


def esta_bloqueado(conn: sqlite3.Connection, telefone: str) -> bool:
    linha = conn.execute(
        "SELECT 1 FROM blocklist WHERE telefone_e164 = ?", (telefone,)
    ).fetchone()
    return linha is not None
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_blocklist.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/blocklist.py tests/test_blocklist.py
git commit -m "feat: blocklist permanente de telefones"
```

---

### Task 4: Importador de CSV

**Files:**
- Create: `src/disparo/importador.py`, `tests/test_importador.py`

**Interfaces:**
- Consumes: `telefone.normalizar` (Task 2), `blocklist.esta_bloqueado` (Task 3).
- Produces: `importador.RelatorioImportacao` (dataclass com `lidos`, `importados`, `duplicados`, `invalidos`, `bloqueados`), `importador.importar_csv(conn, texto: str, agora: datetime) -> RelatorioImportacao`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_importador.py
from datetime import datetime

from disparo.blocklist import bloquear
from disparo.importador import importar_csv

AGORA = datetime(2026, 8, 4, 9, 0)

CSV = """nome,telefone,veiculo
Joao Silva,(11) 98888-4444,Onix 2019
Maria Souza,11977773333,HB20 2021
Joao Silva,11988884444,Onix 2019
Sem Telefone,abc,Gol 2015
Bloqueado,11966662222,Palio 2012
"""


def test_importa_normaliza_e_conta(conn):
    bloquear(conn, "5511966662222", "opt_out", AGORA)
    rel = importar_csv(conn, CSV, AGORA)
    assert rel.lidos == 5
    assert rel.importados == 2
    assert rel.duplicados == 1
    assert rel.invalidos == 1
    assert rel.bloqueados == 1


def test_bloqueado_nao_entra(conn):
    bloquear(conn, "5511966662222", "opt_out", AGORA)
    importar_csv(conn, CSV, AGORA)
    linha = conn.execute(
        "SELECT 1 FROM leads WHERE telefone_e164 = ?", ("5511966662222",)
    ).fetchone()
    assert linha is None


def test_reimportar_o_mesmo_arquivo_nao_duplica(conn):
    importar_csv(conn, CSV, AGORA)
    rel = importar_csv(conn, CSV, AGORA)
    assert rel.importados == 0
    total = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    assert total == 2


def test_cabecalho_com_maiuscula_e_espaco(conn):
    csv = "Nome, Telefone , Veiculo\nAna,11955551111,Kwid 2020\n"
    rel = importar_csv(conn, csv, AGORA)
    assert rel.importados == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_importador.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.importador'`

- [ ] **Step 3: Implementar**

```python
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_importador.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/importador.py tests/test_importador.py
git commit -m "feat: importador de CSV com dedup e blocklist"
```

---

### Task 5: Cota diária e rampa

**Files:**
- Create: `src/disparo/cota.py`, `tests/test_cota.py`

**Interfaces:**
- Consumes: fixture `conn`.
- Produces: `cota.definir_inicio(conn, dia: date) -> None`, `cota.limite_do_dia(conn, hoje: date) -> int`, `cota.enviados_no_dia(conn, hoje: date) -> int`, `cota.registrar_envio(conn, hoje: date) -> None`, `cota.tem_cota(conn, hoje: date) -> bool`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_cota.py
from datetime import date

import pytest

from disparo.cota import (definir_inicio, enviados_no_dia, limite_do_dia,
                          registrar_envio, tem_cota)

INICIO = date(2026, 8, 3)


@pytest.mark.parametrize("dias_passados,esperado", [
    (0, 10), (1, 10), (2, 10),
    (3, 20), (6, 20),
    (7, 30), (30, 30),
])
def test_rampa(conn, dias_passados, esperado):
    definir_inicio(conn, INICIO)
    hoje = date.fromordinal(INICIO.toordinal() + dias_passados)
    assert limite_do_dia(conn, hoje) == esperado


def test_sem_inicio_definido_o_limite_e_zero(conn):
    assert limite_do_dia(conn, date(2026, 8, 4)) == 0


def test_contagem_por_dia_e_isolada(conn):
    definir_inicio(conn, INICIO)
    registrar_envio(conn, date(2026, 8, 4))
    registrar_envio(conn, date(2026, 8, 4))
    registrar_envio(conn, date(2026, 8, 5))
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 2
    assert enviados_no_dia(conn, date(2026, 8, 5)) == 1


def test_tem_cota_ate_o_limite(conn):
    definir_inicio(conn, INICIO)
    hoje = INICIO  # limite 10
    for _ in range(10):
        assert tem_cota(conn, hoje) is True
        registrar_envio(conn, hoje)
    assert tem_cota(conn, hoje) is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_cota.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.cota'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/cota.py
from __future__ import annotations

import sqlite3
from datetime import date

CHAVE_INICIO = "inicio_operacao"

RAMPA = ((3, 10), (7, 20))  # (dias_ate, limite); acima disso, TETO
TETO = 30


def definir_inicio(conn: sqlite3.Connection, dia: date) -> None:
    conn.execute(
        "INSERT INTO config (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (CHAVE_INICIO, dia.isoformat()),
    )
    conn.commit()


def _inicio(conn: sqlite3.Connection) -> date | None:
    linha = conn.execute(
        "SELECT valor FROM config WHERE chave = ?", (CHAVE_INICIO,)
    ).fetchone()
    return date.fromisoformat(linha["valor"]) if linha else None


def limite_do_dia(conn: sqlite3.Connection, hoje: date) -> int:
    inicio = _inicio(conn)
    if inicio is None or hoje < inicio:
        return 0
    dias = (hoje - inicio).days
    for ate, limite in RAMPA:
        if dias < ate:
            return limite
    return TETO


def enviados_no_dia(conn: sqlite3.Connection, hoje: date) -> int:
    linha = conn.execute(
        "SELECT quantidade FROM envios_diarios WHERE dia = ?", (hoje.isoformat(),)
    ).fetchone()
    return linha["quantidade"] if linha else 0


def registrar_envio(conn: sqlite3.Connection, hoje: date) -> None:
    conn.execute(
        "INSERT INTO envios_diarios (dia, quantidade) VALUES (?, 1) "
        "ON CONFLICT(dia) DO UPDATE SET quantidade = quantidade + 1",
        (hoje.isoformat(),),
    )
    conn.commit()


def tem_cota(conn: sqlite3.Connection, hoje: date) -> bool:
    return enviados_no_dia(conn, hoje) < limite_do_dia(conn, hoje)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_cota.py -v`
Expected: PASS (10 casos)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/cota.py tests/test_cota.py
git commit -m "feat: cota diaria com rampa de volume"
```

---

### Task 6: Janela de horário

**Files:**
- Create: `src/disparo/janela.py`, `tests/test_janela.py`

**Interfaces:**
- Consumes: nada.
- Produces: `janela.Janela` (dataclass com `inicio: time`, `fim: time`, `dias: frozenset[int]`), `janela.PADRAO: Janela`, `janela.dentro(agora: datetime, j: Janela = PADRAO) -> bool`, `janela.proxima_abertura(agora: datetime, j: Janela = PADRAO) -> datetime`.

- [ ] **Step 1: Escrever o teste que falha**

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_janela.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.janela'`

- [ ] **Step 3: Implementar**

```python
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_janela.py -v`
Expected: PASS (7 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/janela.py tests/test_janela.py
git commit -m "feat: janela de horario comercial"
```

---

### Task 7: Máquina de estados do lead

**Files:**
- Create: `src/disparo/maquina.py`, `tests/test_maquina.py`

**Interfaces:**
- Consumes: fixture `conn`, fixture `lead`.
- Produces: `maquina.Status` (StrEnum com `NOVO`, `CONTATADO`, `EM_CONVERSA`, `QUENTE`, `FRIO`, `OPT_OUT`, `DADO_DESATUALIZADO`, `SEM_RESPOSTA`, `INVALIDO`), `maquina.TERMINAIS: frozenset[Status]`, `maquina.TransicaoInvalida` (exceção), `maquina.transicionar(conn, lead_id: int, para: Status, agora: datetime) -> None`, `maquina.status_de(conn, lead_id: int) -> Status`, `maquina.robo_pode_falar(status: Status) -> bool`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_maquina.py
from datetime import datetime

import pytest

from disparo.maquina import (Status, TransicaoInvalida, robo_pode_falar,
                             status_de, transicionar)

AGORA = datetime(2026, 8, 4, 10, 0)


def test_caminho_feliz(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.QUENTE, AGORA)
    assert status_de(conn, lead) == Status.QUENTE


def test_nao_pode_pular_de_novo_para_quente(conn, lead):
    with pytest.raises(TransicaoInvalida):
        transicionar(conn, lead, Status.QUENTE, AGORA)


def test_estado_terminal_nao_muda_mais(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.OPT_OUT, AGORA)
    with pytest.raises(TransicaoInvalida):
        transicionar(conn, lead, Status.EM_CONVERSA, AGORA)


def test_robo_so_fala_em_dois_estados():
    assert robo_pode_falar(Status.CONTATADO) is True
    assert robo_pode_falar(Status.EM_CONVERSA) is True
    for s in (Status.NOVO, Status.QUENTE, Status.FRIO, Status.OPT_OUT,
              Status.SEM_RESPOSTA, Status.DADO_DESATUALIZADO, Status.INVALIDO):
        assert robo_pode_falar(s) is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_maquina.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.maquina'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/maquina.py
from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum


class Status(StrEnum):
    NOVO = "novo"
    CONTATADO = "contatado"
    EM_CONVERSA = "em_conversa"
    QUENTE = "quente"
    FRIO = "frio"
    OPT_OUT = "opt_out"
    DADO_DESATUALIZADO = "dado_desatualizado"
    SEM_RESPOSTA = "sem_resposta"
    INVALIDO = "invalido"


TERMINAIS = frozenset({
    Status.QUENTE, Status.FRIO, Status.OPT_OUT,
    Status.DADO_DESATUALIZADO, Status.SEM_RESPOSTA, Status.INVALIDO,
})

_FECHAMENTOS = frozenset({
    Status.QUENTE, Status.FRIO, Status.OPT_OUT, Status.DADO_DESATUALIZADO,
})

TRANSICOES: dict[Status, frozenset[Status]] = {
    Status.NOVO: frozenset({Status.CONTATADO, Status.INVALIDO}),
    Status.CONTATADO: frozenset({Status.EM_CONVERSA, Status.SEM_RESPOSTA} | _FECHAMENTOS),
    Status.EM_CONVERSA: frozenset({Status.SEM_RESPOSTA} | _FECHAMENTOS),
}


class TransicaoInvalida(RuntimeError):
    pass


def status_de(conn: sqlite3.Connection, lead_id: int) -> Status:
    linha = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if linha is None:
        raise TransicaoInvalida(f"lead {lead_id} não existe")
    return Status(linha["status"])


def transicionar(conn: sqlite3.Connection, lead_id: int, para: Status,
                 agora: datetime) -> None:
    atual = status_de(conn, lead_id)
    permitidos = TRANSICOES.get(atual, frozenset())
    if para not in permitidos:
        raise TransicaoInvalida(f"{atual} -> {para} não é permitido")
    conn.execute(
        "UPDATE leads SET status = ?, ultimo_evento_em = ? WHERE id = ?",
        (para.value, agora.isoformat(), lead_id),
    )
    conn.commit()


def robo_pode_falar(status: Status) -> bool:
    return status in (Status.CONTATADO, Status.EM_CONVERSA)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_maquina.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/maquina.py tests/test_maquina.py
git commit -m "feat: maquina de estados do lead"
```

---

### Task 8: Eventos e disjuntor

**Files:**
- Create: `src/disparo/eventos.py`, `src/disparo/disjuntor.py`, `tests/test_disjuntor.py`

**Interfaces:**
- Consumes: fixture `conn`.
- Produces: `eventos.registrar(conn, tipo: str, texto: str, agora: datetime, lead_id: int | None = None) -> None`, `eventos.listar(conn, limite: int = 50) -> list[dict]`, `disjuntor.Veredito` (dataclass `ok: bool`, `motivo: str | None`), `disjuntor.avaliar(conn, amostra: int = 50) -> Veredito`, `disjuntor.pausar(conn, motivo: str, agora: datetime) -> None`, `disjuntor.retomar(conn, agora: datetime) -> None`, `disjuntor.esta_pausado(conn) -> bool`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_disjuntor.py
from datetime import datetime

from disparo.disjuntor import avaliar, esta_pausado, pausar, retomar
from disparo.eventos import listar, registrar

AGORA = datetime(2026, 8, 4, 12, 0)


def _semear(conn, contatados: int, responderam: int, opt_outs: int):
    for i in range(contatados):
        conn.execute(
            "INSERT INTO leads (nome, telefone_e164, veiculo, status, criado_em, "
            "contatado_em) VALUES (?, ?, '', ?, ?, ?)",
            (f"L{i}", f"55119{i:08d}",
             "opt_out" if i < opt_outs else
             ("em_conversa" if i < responderam else "contatado"),
             AGORA.isoformat(), AGORA.isoformat()),
        )
    conn.commit()


def test_tudo_bem(conn):
    _semear(conn, contatados=50, responderam=15, opt_outs=0)
    assert avaliar(conn).ok is True


def test_resposta_baixa_dispara(conn):
    _semear(conn, contatados=50, responderam=4, opt_outs=0)
    v = avaliar(conn)
    assert v.ok is False
    assert "resposta" in v.motivo


def test_muitos_opt_outs_disparam(conn):
    _semear(conn, contatados=50, responderam=20, opt_outs=3)
    v = avaliar(conn)
    assert v.ok is False
    assert "opt-out" in v.motivo


def test_poucos_contatos_nao_disparam(conn):
    _semear(conn, contatados=5, responderam=0, opt_outs=0)
    assert avaliar(conn).ok is True


def test_pausar_e_retomar(conn):
    assert esta_pausado(conn) is False
    pausar(conn, "teste", AGORA)
    assert esta_pausado(conn) is True
    retomar(conn, AGORA)
    assert esta_pausado(conn) is False


def test_pausar_registra_evento(conn):
    pausar(conn, "taxa de resposta baixa", AGORA)
    eventos = listar(conn)
    assert eventos[0]["tipo"] == "alerta"
    assert "taxa de resposta baixa" in eventos[0]["texto"]


def test_listar_devolve_mais_recente_primeiro(conn):
    registrar(conn, "sistema", "primeiro", datetime(2026, 8, 4, 9, 0))
    registrar(conn, "sistema", "segundo", datetime(2026, 8, 4, 10, 0))
    assert listar(conn)[0]["texto"] == "segundo"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_disjuntor.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.disjuntor'`

- [ ] **Step 3: Implementar `eventos.py`**

```python
# src/disparo/eventos.py
from __future__ import annotations

import sqlite3
from datetime import datetime


def registrar(conn: sqlite3.Connection, tipo: str, texto: str, agora: datetime,
              lead_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO eventos (tipo, lead_id, texto, criado_em) VALUES (?, ?, ?, ?)",
        (tipo, lead_id, texto, agora.isoformat()),
    )
    conn.commit()


def listar(conn: sqlite3.Connection, limite: int = 50) -> list[dict]:
    linhas = conn.execute(
        "SELECT id, tipo, lead_id, texto, criado_em FROM eventos "
        "ORDER BY id DESC LIMIT ?",
        (limite,),
    ).fetchall()
    return [dict(linha) for linha in linhas]
```

- [ ] **Step 4: Implementar `disjuntor.py`**

```python
# src/disparo/disjuntor.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from disparo.eventos import registrar

CHAVE_PAUSADO = "pausado"
MINIMO_PARA_AVALIAR = 20
PISO_RESPOSTA = 0.10
TETO_OPT_OUT = 3

_RESPONDERAM = ("em_conversa", "quente", "frio", "opt_out", "dado_desatualizado")


@dataclass(frozen=True)
class Veredito:
    ok: bool
    motivo: str | None = None


def avaliar(conn: sqlite3.Connection, amostra: int = 50) -> Veredito:
    linhas = conn.execute(
        "SELECT status FROM leads WHERE contatado_em IS NOT NULL "
        "ORDER BY id DESC LIMIT ?",
        (amostra,),
    ).fetchall()
    if len(linhas) < MINIMO_PARA_AVALIAR:
        return Veredito(True)

    status = [linha["status"] for linha in linhas]
    responderam = sum(1 for s in status if s in _RESPONDERAM)
    opt_outs = sum(1 for s in status if s == "opt_out")

    if opt_outs >= TETO_OPT_OUT:
        return Veredito(False, f"{opt_outs} opt-out nos últimos {len(status)} disparos")
    taxa = responderam / len(status)
    if taxa < PISO_RESPOSTA:
        return Veredito(
            False, f"taxa de resposta em {taxa:.0%} nos últimos {len(status)} disparos"
        )
    return Veredito(True)


def esta_pausado(conn: sqlite3.Connection) -> bool:
    linha = conn.execute(
        "SELECT valor FROM config WHERE chave = ?", (CHAVE_PAUSADO,)
    ).fetchone()
    return bool(linha) and linha["valor"] == "1"


def _definir(conn: sqlite3.Connection, valor: str) -> None:
    conn.execute(
        "INSERT INTO config (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (CHAVE_PAUSADO, valor),
    )
    conn.commit()


def pausar(conn: sqlite3.Connection, motivo: str, agora: datetime) -> None:
    _definir(conn, "1")
    registrar(conn, "alerta", f"Operação pausada: {motivo}", agora)


def retomar(conn: sqlite3.Connection, agora: datetime) -> None:
    _definir(conn, "0")
    registrar(conn, "sistema", "Operação retomada", agora)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/test_disjuntor.py -v`
Expected: PASS (7 testes)

- [ ] **Step 6: Commit**

```bash
git add src/disparo/eventos.py src/disparo/disjuntor.py tests/test_disjuntor.py
git commit -m "feat: eventos e disjuntor de protecao"
```

---

### Task 9: Comportamento humano

**Files:**
- Create: `src/disparo/humano.py`, `tests/test_humano.py`

**Interfaces:**
- Consumes: nada.
- Produces: `humano.atraso_leitura(rng) -> float`, `humano.atraso_resposta(rng) -> float`, `humano.duracao_digitando(texto: str, rng) -> float`, `humano.intervalo_entre_disparos(rng) -> float`, `humano.quebrar(texto: str, limite: int = 160) -> list[str]`. Todas recebem um `random.Random` para serem determinísticas em teste.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_humano.py
import random

from disparo.humano import (atraso_leitura, atraso_resposta,
                            duracao_digitando, intervalo_entre_disparos,
                            quebrar)

RNG = random.Random(42)


def test_faixas():
    for _ in range(200):
        assert 3 <= atraso_leitura(RNG) <= 20
        assert 15 <= atraso_resposta(RNG) <= 180
        assert 120 <= intervalo_entre_disparos(RNG) <= 480


def test_digitando_cresce_com_o_texto():
    curto = duracao_digitando("oi", RNG)
    longo = duracao_digitando("x" * 300, RNG)
    assert longo > curto
    assert 2 <= curto <= 8 and 2 <= longo <= 8


def test_texto_curto_nao_quebra():
    assert quebrar("Tudo bem também.") == ["Tudo bem também."]


def test_texto_longo_quebra_em_frases():
    texto = ("Perguntei porque eu trabalho na Porto Sul, de proteção veicular. "
             "A gente dá pretinho e cheirinho de graça de 6 em 6 meses pra quem "
             "é associado. Mas o principal é a proteção em si.")
    partes = quebrar(texto, limite=120)
    assert len(partes) >= 2
    assert all(len(p) <= 160 for p in partes)
    assert "".join(partes).replace(" ", "") == texto.replace(" ", "")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_humano.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.humano'`

- [ ] **Step 3: Implementar**

```python
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
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_humano.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/humano.py tests/test_humano.py
git commit -m "feat: atrasos e quebra de mensagem para comportamento humano"
```

---

### Task 10: Cliente da Evolution API

**Files:**
- Create: `src/disparo/evolution.py`, `tests/test_evolution.py`

**Interfaces:**
- Consumes: nada do projeto; usa `httpx`.
- Produces: `evolution.Evolution` com `numero_existe(telefone: str) -> bool`, `enviar_texto(telefone: str, texto: str) -> str` (devolve `wa_message_id`), `marcar_lida(telefone: str, wa_message_id: str) -> None`, `digitando(telefone: str, segundos: float) -> None`, e a exceção `evolution.EvolutionIndisponivel`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_evolution.py
import httpx
import pytest
import respx

from disparo.evolution import Evolution, EvolutionIndisponivel

BASE = "http://evo:8080"


def _cliente() -> Evolution:
    return Evolution(BASE, "chave", "portosul-01", httpx.Client(timeout=5))


@respx.mock
def test_numero_existe():
    respx.post(f"{BASE}/chat/whatsappNumbers/portosul-01").mock(
        return_value=httpx.Response(200, json=[{"exists": True, "jid": "x"}])
    )
    assert _cliente().numero_existe("5511988884444") is True


@respx.mock
def test_numero_nao_existe():
    respx.post(f"{BASE}/chat/whatsappNumbers/portosul-01").mock(
        return_value=httpx.Response(200, json=[{"exists": False}])
    )
    assert _cliente().numero_existe("5511988884444") is False


@respx.mock
def test_enviar_texto_devolve_id():
    rota = respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(201, json={"key": {"id": "WA123"}})
    )
    assert _cliente().enviar_texto("5511988884444", "oi") == "WA123"
    assert rota.calls.last.request.headers["apikey"] == "chave"


@respx.mock
def test_erro_de_rede_vira_excecao_do_dominio():
    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        side_effect=httpx.ConnectError("sem rede")
    )
    with pytest.raises(EvolutionIndisponivel):
        _cliente().enviar_texto("5511988884444", "oi")


@respx.mock
def test_status_5xx_vira_excecao_do_dominio():
    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )
    with pytest.raises(EvolutionIndisponivel):
        _cliente().enviar_texto("5511988884444", "oi")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_evolution.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.evolution'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/evolution.py
from __future__ import annotations

import httpx


class EvolutionIndisponivel(RuntimeError):
    """A Evolution API não respondeu, ou respondeu com erro de servidor."""


class Evolution:
    def __init__(self, base_url: str, api_key: str, instancia: str,
                 http: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._instancia = instancia
        self._http = http
        self._cabecalhos = {"apikey": api_key, "Content-Type": "application/json"}

    def _post(self, caminho: str, corpo: dict) -> httpx.Response:
        url = f"{self._base}/{caminho}/{self._instancia}"
        try:
            resposta = self._http.post(url, json=corpo, headers=self._cabecalhos)
        except httpx.HTTPError as erro:
            raise EvolutionIndisponivel(str(erro)) from erro
        if resposta.status_code >= 500:
            raise EvolutionIndisponivel(
                f"{resposta.status_code} em {caminho}: {resposta.text[:200]}"
            )
        resposta.raise_for_status()
        return resposta

    def numero_existe(self, telefone: str) -> bool:
        dados = self._post("chat/whatsappNumbers", {"numbers": [telefone]}).json()
        return bool(dados) and bool(dados[0].get("exists"))

    def enviar_texto(self, telefone: str, texto: str) -> str:
        dados = self._post(
            "message/sendText", {"number": telefone, "text": texto}
        ).json()
        return dados["key"]["id"]

    def marcar_lida(self, telefone: str, wa_message_id: str) -> None:
        self._post("chat/markMessageAsRead", {
            "readMessages": [
                {"remoteJid": f"{telefone}@s.whatsapp.net",
                 "id": wa_message_id, "fromMe": False}
            ]
        })

    def digitando(self, telefone: str, segundos: float) -> None:
        self._post("chat/sendPresence", {
            "number": telefone,
            "presence": "composing",
            "delay": int(segundos * 1000),
        })
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_evolution.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/evolution.py tests/test_evolution.py
git commit -m "feat: cliente HTTP da Evolution API"
```

---

### Task 11: Normalizador de mídia

**Files:**
- Create: `src/disparo/midia.py`, `tests/test_midia.py`

**Interfaces:**
- Consumes: nada do projeto.
- Produces: `midia.MensagemNormalizada` (dataclass `tipo: str`, `texto: str`, `imagem_b64: str | None`, `media_type: str | None`, `wa_message_id: str`, `telefone: str`), `midia.normalizar(payload: dict, transcritor: Callable[[bytes], str]) -> MensagemNormalizada | None`, `midia.transcritor_whisper(modelo: str) -> Callable[[bytes], str]`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_midia.py
import base64

from disparo.midia import normalizar


def _envelope(interno: dict, id_: str = "WA1") -> dict:
    return {"data": {"key": {"id": id_, "remoteJid": "5511988884444@s.whatsapp.net",
                             "fromMe": False},
                     "message": interno}}


def test_texto_simples():
    m = normalizar(_envelope({"conversation": "tudo bem e vc"}), lambda b: "")
    assert m.tipo == "texto"
    assert m.texto == "tudo bem e vc"
    assert m.telefone == "5511988884444"
    assert m.wa_message_id == "WA1"


def test_texto_estendido():
    m = normalizar(
        _envelope({"extendedTextMessage": {"text": "passo sim"}}), lambda b: ""
    )
    assert m.texto == "passo sim"


def test_audio_e_transcrito():
    payload = _envelope({"audioMessage": {"mimetype": "audio/ogg"},
                         "base64": base64.b64encode(b"bytes").decode()})
    m = normalizar(payload, lambda b: "passo sim toda semana")
    assert m.tipo == "audio"
    assert m.texto == "passo sim toda semana"


def test_imagem_vira_bloco_de_imagem():
    payload = _envelope({"imageMessage": {"mimetype": "image/jpeg"},
                         "base64": base64.b64encode(b"jpg").decode()})
    m = normalizar(payload, lambda b: "")
    assert m.tipo == "imagem"
    assert m.media_type == "image/jpeg"
    assert m.imagem_b64 is not None


def test_figurinha_vira_reacao():
    m = normalizar(_envelope({"stickerMessage": {}}), lambda b: "")
    assert m.tipo == "figurinha"
    assert m.texto == "[o lead enviou uma figurinha]"


def test_video_pede_texto():
    m = normalizar(_envelope({"videoMessage": {}}), lambda b: "")
    assert m.tipo == "video"
    assert "vídeo" in m.texto


def test_mensagem_propria_e_ignorada():
    payload = _envelope({"conversation": "oi"})
    payload["data"]["key"]["fromMe"] = True
    assert normalizar(payload, lambda b: "") is None


def test_transcricao_que_falha_nao_derruba():
    def quebrado(_: bytes) -> str:
        raise RuntimeError("whisper caiu")

    payload = _envelope({"audioMessage": {"mimetype": "audio/ogg"},
                         "base64": base64.b64encode(b"x").decode()})
    m = normalizar(payload, quebrado)
    assert m.tipo == "audio"
    assert "não consegui ouvir" in m.texto
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_midia.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.midia'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/midia.py
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class MensagemNormalizada:
    tipo: str
    texto: str
    telefone: str
    wa_message_id: str
    imagem_b64: str | None = None
    media_type: str | None = None


def _telefone(jid: str) -> str:
    return jid.split("@", 1)[0].split(":", 1)[0]


def normalizar(payload: dict,
               transcritor: Callable[[bytes], str]) -> MensagemNormalizada | None:
    dados = payload.get("data") or {}
    chave = dados.get("key") or {}
    if chave.get("fromMe"):
        return None

    telefone = _telefone(chave.get("remoteJid", ""))
    wa_id = chave.get("id", "")
    msg = dados.get("message") or {}
    comum = {"telefone": telefone, "wa_message_id": wa_id}

    if "conversation" in msg:
        return MensagemNormalizada("texto", msg["conversation"], **comum)

    if "extendedTextMessage" in msg:
        texto = msg["extendedTextMessage"].get("text", "")
        return MensagemNormalizada("texto", texto, **comum)

    if "audioMessage" in msg:
        try:
            bruto = base64.b64decode(dados.get("base64", ""))
            texto = transcritor(bruto).strip()
        except Exception:
            texto = ""
        if not texto:
            texto = "[áudio que não consegui ouvir]"
        return MensagemNormalizada("audio", texto, **comum)

    if "imageMessage" in msg:
        return MensagemNormalizada(
            "imagem",
            "[o lead enviou uma foto]",
            imagem_b64=dados.get("base64"),
            media_type=msg["imageMessage"].get("mimetype", "image/jpeg"),
            **comum,
        )

    if "stickerMessage" in msg:
        return MensagemNormalizada("figurinha", "[o lead enviou uma figurinha]", **comum)

    if "videoMessage" in msg:
        return MensagemNormalizada(
            "video", "[o lead enviou um vídeo, que não consigo assistir]", **comum
        )

    if "locationMessage" in msg:
        return MensagemNormalizada("localizacao", "[o lead enviou uma localização]", **comum)

    if "contactMessage" in msg:
        return MensagemNormalizada("contato", "[o lead enviou um contato]", **comum)

    return MensagemNormalizada("outro", "[o lead enviou algo que não consigo ler]", **comum)


def transcritor_whisper(modelo: str = "small") -> Callable[[bytes], str]:
    """Devolve um transcritor que roda faster-whisper local, em português."""
    from faster_whisper import WhisperModel

    whisper = WhisperModel(modelo, device="cpu", compute_type="int8")

    def transcrever(audio: bytes) -> str:
        segmentos, _ = whisper.transcribe(io.BytesIO(audio), language="pt")
        return " ".join(s.text for s in segmentos).strip()

    return transcrever
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_midia.py -v`
Expected: PASS (8 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/midia.py tests/test_midia.py
git commit -m "feat: normalizador de midia com transcricao local de audio"
```

---

### Task 12: Conversador (Haiku)

**Files:**
- Create: `src/disparo/conversador.py`, `tests/test_conversador.py`

**Interfaces:**
- Consumes: `maquina.Status` (Task 7).
- Produces: `conversador.Qualificacao` (modelo pydantic com `resposta: str`, `decisao: Literal["continuar","quente","frio","opt_out","dado_desatualizado"]`, `resumo: str`, `paga_hoje: str | None`, `tem_cobertura: Literal["sim","nao","nao_informado"]`, `carro_quitado: Literal["quitado","financiado","nao_informado"]`), `conversador.PROMPT: str`, `conversador.abertura(nome: str, rng) -> str`, `conversador.conversar(cliente, lead: dict, historico: list[dict]) -> Qualificacao`, `conversador.TETO_TURNOS: int = 12`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_conversador.py
import random
from types import SimpleNamespace

import pytest

from disparo.conversador import (PROMPT, Qualificacao, abertura, conversar)


class ClienteFalso:
    """Duplo do cliente Anthropic. Guarda a chamada e devolve o que mandarmos."""

    def __init__(self, resultado: Qualificacao):
        self.resultado = resultado
        self.chamada: dict | None = None
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.chamada = kwargs
        return SimpleNamespace(parsed_output=self.resultado)


LEAD = {"nome": "Joao", "veiculo": "Onix 2019", "turnos": 2}
HISTORICO = [
    {"direcao": "saida", "texto": "Oii Joao, tudo bem?"},
    {"direcao": "entrada", "texto": "tudo bem e vc"},
]


def test_abertura_usa_o_nome_e_varia():
    rng = random.Random(1)
    textos = {abertura("Joao", rng) for _ in range(50)}
    assert len(textos) >= 2
    assert all("Joao" in t for t in textos)
    assert all("Porto Sul" not in t for t in textos)


def test_conversar_usa_o_modelo_certo_e_cacheia_o_prompt():
    cliente = ClienteFalso(Qualificacao(
        resposta="Tudo bem também.", decisao="continuar", resumo="",
        paga_hoje=None, tem_cobertura="nao_informado", carro_quitado="nao_informado",
    ))
    conversar(cliente, LEAD, HISTORICO)
    chamada = cliente.chamada
    assert chamada["model"] == "claude-haiku-4-5"
    assert chamada["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert chamada["output_format"] is Qualificacao


def test_historico_vira_papeis_alternados():
    cliente = ClienteFalso(Qualificacao(
        resposta="ok", decisao="continuar", resumo="",
        paga_hoje=None, tem_cobertura="nao_informado", carro_quitado="nao_informado",
    ))
    conversar(cliente, LEAD, HISTORICO)
    mensagens = cliente.chamada["messages"]
    assert mensagens[0]["role"] == "assistant"
    assert mensagens[1]["role"] == "user"


def test_imagem_entra_como_bloco_de_imagem():
    cliente = ClienteFalso(Qualificacao(
        resposta="ok", decisao="continuar", resumo="",
        paga_hoje=None, tem_cobertura="nao_informado", carro_quitado="nao_informado",
    ))
    historico = HISTORICO + [{
        "direcao": "entrada", "texto": "[o lead enviou uma foto]",
        "imagem_b64": "QUJD", "media_type": "image/jpeg",
    }]
    conversar(cliente, LEAD, historico)
    ultimo = cliente.chamada["messages"][-1]["content"]
    assert any(bloco["type"] == "image" for bloco in ultimo)


def test_prompt_contem_as_regras_criticas():
    for trecho in ["Porto Sul", "pretinho", "nunca invente", "automat"]:
        assert trecho.lower() in PROMPT.lower()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_conversador.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.conversador'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/conversador.py
from __future__ import annotations

import random
from typing import Any, Literal

from pydantic import BaseModel, Field

MODELO = "claude-haiku-4-5"
TETO_TURNOS = 12

ABERTURAS = ("Oii {nome}, tudo bem?", "Oi {nome}, tudo bem?", "Bom dia {nome}, tudo bem?")

PROMPT = """\
Você conversa por WhatsApp em nome da Porto Sul, empresa de proteção veicular.
O objetivo é descobrir se a pessoa tem interesse em uma cotação, e passar para a
vendedora quando tiver. Você não vende, não fecha e não calcula preço.

# Como a conversa anda
Etapa 1 — confirmar o veículo: "Vi aqui que você tem um {veiculo}, certo?"
  - Se confirmar, vá para a etapa 2.
  - Se confirmar e perguntar o motivo, PULE a etapa 2 e vá direto para a etapa 3.
  - Se disser que não é o carro dele, se desculpe e encerre com decisao=dado_desatualizado.
Etapa 2 — quebra-gelo: "Você passa pretinho no pneu?"
Etapa 3 — identificação: diga que trabalha na Porto Sul, de proteção veicular, e que
  a empresa dá pretinho e cheirinho de graça de 6 em 6 meses para quem é associado.
  Comece a frase acompanhando o tom dele: "Haha boa." se ele foi descontraído,
  "Boa." se foi seco, "Tranquilo." se disse que não passa pretinho.
Etapa 4 — oferta: diga que o principal não é o pretinho e sim a proteção, que costuma
  sair bem abaixo de seguro, e pergunte se o carro está protegido por alguma coisa hoje.
  Conforme a resposta, pergunte quanto ele paga por mês e se o carro é quitado ou financiado.
Etapa 5 — fechamento: ofereça montar a cotação, sem compromisso.

# Como escrever
Português neutro e educado, como uma pessoa escrevendo no WhatsApp. Voz feminina —
use concordância no feminino e "obrigada". Frases curtas, no máximo duas linhas.
Pode usar "tranquilo", "boa", "haha", "perfeito". Não use gíria pesada ("salve",
"suave", "firmeza", "mano", "top") nem linguagem formal ("prezado", "venho por meio
desta"). Sem emoji nas duas primeiras mensagens; depois no máximo um, e só se ele usar
primeiro. Sem caixa alta e sem exclamação dupla. Espelhe o registro do lead.

# Proibições
- Nunca invente preço, valor de mensalidade ou desconto. Quem calcula é a vendedora.
- Nunca prometa cobertura nem afirme o que está incluso.
- Só comente que o valor que ele paga é alto se for realmente alto. Se for um valor
  normal, responda de forma neutra e siga.
- Se ele perguntar se você é um robô, um bot ou se a mensagem é automática, responda a
  verdade: a primeira abordagem é automatizada e a vendedora assume em seguida. Nunca negue.
- Nunca insista depois de uma recusa.

# Classificação
decisao=quente quando ele aceita a cotação, pergunta preço, demonstra achar caro o que
paga, não tem cobertura e demonstra interesse, ou teve seguro recusado.
decisao=frio quando responde sem interesse ou já está satisfeito com o que tem.
decisao=opt_out em qualquer pedido para parar de receber mensagem.
decisao=dado_desatualizado quando o veículo não é dele.
decisao=continuar no resto.

O campo resposta é exatamente o texto que será enviado ao lead. Se a decisão for
quente, a resposta deve avisar que a cotação será montada e enviada.
"""


class Qualificacao(BaseModel):
    resposta: str = Field(description="Texto a enviar ao lead")
    decisao: Literal["continuar", "quente", "frio", "opt_out", "dado_desatualizado"]
    resumo: str = Field(description="Uma linha para a vendedora")
    paga_hoje: str | None = Field(default=None, description="Valor mensal atual")
    tem_cobertura: Literal["sim", "nao", "nao_informado"] = "nao_informado"
    carro_quitado: Literal["quitado", "financiado", "nao_informado"] = "nao_informado"


def abertura(nome: str, rng: random.Random) -> str:
    return rng.choice(ABERTURAS).format(nome=nome)


def _conteudo(mensagem: dict) -> list[dict[str, Any]]:
    blocos: list[dict[str, Any]] = []
    if mensagem.get("imagem_b64"):
        blocos.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mensagem.get("media_type", "image/jpeg"),
                "data": mensagem["imagem_b64"],
            },
        })
    blocos.append({"type": "text", "text": mensagem.get("texto", "")})
    return blocos


def conversar(cliente: Any, lead: dict, historico: list[dict]) -> Qualificacao:
    """Uma chamada ao Haiku com o histórico completo daquela conversa."""
    sistema = PROMPT.replace("{veiculo}", lead.get("veiculo") or "seu carro")
    mensagens = [
        {
            "role": "assistant" if m["direcao"] == "saida" else "user",
            "content": _conteudo(m),
        }
        for m in historico
    ]
    resposta = cliente.messages.parse(
        model=MODELO,
        max_tokens=512,
        system=[{
            "type": "text",
            "text": sistema,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=mensagens,
        output_format=Qualificacao,
    )
    return resposta.parsed_output
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_conversador.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/conversador.py tests/test_conversador.py
git commit -m "feat: conversador com prompt do roteiro e saida estruturada"
```

---

### Task 13: Agendador de disparo

**Files:**
- Create: `src/disparo/agendador.py`, `tests/test_agendador.py`

**Interfaces:**
- Consumes: `cota`, `janela`, `disjuntor`, `maquina`, `evolution`, `humano`, `conversador.abertura`, `eventos`.
- Produces: `agendador.Resultado` (dataclass `enviou: bool`, `motivo: str`), `agendador.tentar_disparar(conn, evo, agora: datetime, rng: random.Random) -> Resultado`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_agendador.py
import random
from datetime import date, datetime

from disparo.agendador import tentar_disparar
from disparo.cota import definir_inicio, enviados_no_dia
from disparo.disjuntor import pausar
from disparo.maquina import Status, status_de

RNG = random.Random(7)
TERCA_10H = datetime(2026, 8, 4, 10, 0)


class EvoFalsa:
    def __init__(self, existe=True, falha=False):
        self.existe = existe
        self.falha = falha
        self.enviados: list[tuple[str, str]] = []

    def numero_existe(self, telefone):
        return self.existe

    def enviar_texto(self, telefone, texto):
        if self.falha:
            from disparo.evolution import EvolutionIndisponivel
            raise EvolutionIndisponivel("caiu")
        self.enviados.append((telefone, texto))
        return f"WA{len(self.enviados)}"


def test_dispara_lead_novo(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    evo = EvoFalsa()
    r = tentar_disparar(conn, evo, TERCA_10H, RNG)
    assert r.enviou is True
    assert len(evo.enviados) == 1
    assert "Joao" in evo.enviados[0][1]
    assert status_de(conn, lead) == Status.CONTATADO
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 1


def test_nao_dispara_fora_da_janela(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    evo = EvoFalsa()
    r = tentar_disparar(conn, evo, datetime(2026, 8, 4, 20, 0), RNG)
    assert r.enviou is False
    assert r.motivo == "fora da janela"
    assert evo.enviados == []


def test_nao_dispara_pausado(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    pausar(conn, "teste", TERCA_10H)
    r = tentar_disparar(conn, EvoFalsa(), TERCA_10H, RNG)
    assert r.enviou is False
    assert r.motivo == "pausado"


def test_numero_inexistente_nao_gasta_cota(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    evo = EvoFalsa(existe=False)
    r = tentar_disparar(conn, evo, TERCA_10H, RNG)
    assert r.enviou is False
    assert status_de(conn, lead) == Status.INVALIDO
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 0


def test_falha_de_envio_nao_gasta_cota_e_pausa(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    r = tentar_disparar(conn, EvoFalsa(falha=True), TERCA_10H, RNG)
    assert r.enviou is False
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 0
    assert status_de(conn, lead) == Status.NOVO


def test_sem_lead_novo(conn):
    definir_inicio(conn, date(2026, 8, 3))
    r = tentar_disparar(conn, EvoFalsa(), TERCA_10H, RNG)
    assert r.enviou is False
    assert r.motivo == "sem lead novo"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_agendador.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.agendador'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/agendador.py
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from disparo import cota, disjuntor, eventos, janela
from disparo.conversador import abertura
from disparo.evolution import EvolutionIndisponivel
from disparo.maquina import Status, transicionar


@dataclass(frozen=True)
class Resultado:
    enviou: bool
    motivo: str


def _proximo_lead(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, nome, telefone_e164, veiculo FROM leads "
        "WHERE status = 'novo' ORDER BY id LIMIT 1"
    ).fetchone()


def tentar_disparar(conn: sqlite3.Connection, evo, agora: datetime,
                    rng: random.Random) -> Resultado:
    """Envia no máximo uma mensagem de abertura. Chamado a cada minuto."""
    if disjuntor.esta_pausado(conn):
        return Resultado(False, "pausado")

    if not janela.dentro(agora):
        return Resultado(False, "fora da janela")

    veredito = disjuntor.avaliar(conn)
    if not veredito.ok:
        disjuntor.pausar(conn, veredito.motivo or "sinal ruim", agora)
        return Resultado(False, "disjuntor disparou")

    if not cota.tem_cota(conn, agora.date()):
        return Resultado(False, "cota do dia esgotada")

    lead = _proximo_lead(conn)
    if lead is None:
        return Resultado(False, "sem lead novo")

    try:
        if not evo.numero_existe(lead["telefone_e164"]):
            transicionar(conn, lead["id"], Status.INVALIDO, agora)
            eventos.registrar(
                conn, "sistema",
                f"{lead['telefone_e164']} não existe no WhatsApp — cota preservada",
                agora, lead["id"],
            )
            return Resultado(False, "numero inexistente")

        texto = abertura(lead["nome"].split()[0], rng)
        wa_id = evo.enviar_texto(lead["telefone_e164"], texto)
    except EvolutionIndisponivel as erro:
        eventos.registrar(conn, "alerta", f"Falha ao enviar: {erro}", agora, lead["id"])
        return Resultado(False, "evolution indisponivel")

    conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, wa_message_id, criado_em) "
        "VALUES (?, 'saida', 'texto', ?, ?, ?)",
        (lead["id"], texto, wa_id, agora.isoformat()),
    )
    conn.execute(
        "UPDATE leads SET contatado_em = ?, etapa = 1 WHERE id = ?",
        (agora.isoformat(), lead["id"]),
    )
    conn.commit()
    transicionar(conn, lead["id"], Status.CONTATADO, agora)
    cota.registrar_envio(conn, agora.date())
    eventos.registrar(
        conn, "envio",
        f"Disparo para {lead['nome']} — {lead['veiculo']}", agora, lead["id"],
    )
    return Resultado(True, "enviado")
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_agendador.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/agendador.py tests/test_agendador.py
git commit -m "feat: agendador de disparo com todas as travas"
```

---

### Task 14: Handoff e resposta ao lead

**Files:**
- Create: `src/disparo/handoff.py`, `src/disparo/resposta.py`, `tests/test_resposta.py`

**Interfaces:**
- Consumes: `conversador.Qualificacao`, `maquina`, `humano`, `evolution`, `eventos`, `blocklist`.
- Produces: `handoff.avisar_vendedora(conn, evo, telefone_vendedora: str, lead: dict, qualificacao, agora) -> None`, `resposta.processar(conn, evo, cliente_claude, cfg, mensagem: MensagemNormalizada, agora: datetime, rng, dormir=time.sleep) -> None`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_resposta.py
import random
from datetime import datetime
from types import SimpleNamespace

from disparo.blocklist import esta_bloqueado
from disparo.conversador import Qualificacao
from disparo.maquina import Status, status_de, transicionar
from disparo.midia import MensagemNormalizada
from disparo.resposta import processar

RNG = random.Random(3)
AGORA = datetime(2026, 8, 4, 11, 0)
CFG = SimpleNamespace(vendedora_telefone="5511900000000")


class EvoFalsa:
    def __init__(self):
        self.enviados: list[tuple[str, str]] = []
        self.lidas: list[str] = []
        self.digitou: list[float] = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return f"WA{len(self.enviados)}"

    def marcar_lida(self, telefone, wa_message_id):
        self.lidas.append(wa_message_id)

    def digitando(self, telefone, segundos):
        self.digitou.append(segundos)


def _claude(qualificacao: Qualificacao):
    return SimpleNamespace(
        messages=SimpleNamespace(
            parse=lambda **kw: SimpleNamespace(parsed_output=qualificacao)
        )
    )


def _msg(texto="tudo bem e vc", wa_id="WA-in-1"):
    return MensagemNormalizada("texto", texto, "5511988884444", wa_id)


def _q(decisao="continuar", resposta="Tudo bem também."):
    return Qualificacao(resposta=resposta, decisao=decisao, resumo="resumo",
                        paga_hoje=None, tem_cobertura="nao_informado",
                        carro_quitado="nao_informado")


def test_responde_e_vai_para_em_conversa(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    assert evo.enviados[0][0] == "5511988884444"
    assert status_de(conn, lead) == Status.EM_CONVERSA
    assert evo.lidas == ["WA-in-1"]
    assert evo.digitou


def test_lead_quente_avisa_a_vendedora_e_silencia(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q("quente", "Perfeito, já te mando.")),
              CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    destinos = [d for d, _ in evo.enviados]
    assert "5511900000000" in destinos
    assert status_de(conn, lead) == Status.QUENTE


def test_opt_out_entra_na_blocklist(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q("opt_out", "Tranquilo, não te incomodo mais.")),
              CFG, _msg("para de mandar"), AGORA, RNG, dormir=lambda s: None)
    assert status_de(conn, lead) == Status.OPT_OUT
    assert esta_bloqueado(conn, "5511988884444") is True


def test_lead_em_estado_terminal_e_ignorado(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.FRIO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    assert evo.enviados == []


def test_mensagem_duplicada_nao_responde_duas_vezes(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    for _ in range(2):
        processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG,
                  dormir=lambda s: None)
    assert len(evo.enviados) == 1


def test_telefone_desconhecido_e_ignorado(conn):
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    assert evo.enviados == []
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_resposta.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.resposta'`

- [ ] **Step 3: Implementar `handoff.py`**

```python
# src/disparo/handoff.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from disparo import eventos
from disparo.conversador import Qualificacao


def avisar_vendedora(conn: sqlite3.Connection, evo, telefone_vendedora: str,
                     lead: sqlite3.Row, qualificacao: Qualificacao,
                     agora: datetime) -> None:
    fone = lead["telefone_e164"]
    linhas = [
        f"Lead quente — {lead['nome']}",
        f"{lead['veiculo']}",
        qualificacao.resumo,
    ]
    if qualificacao.paga_hoje:
        linhas.append(f"Paga hoje: {qualificacao.paga_hoje}")
    if qualificacao.carro_quitado != "nao_informado":
        linhas.append(f"Carro: {qualificacao.carro_quitado}")
    linhas.append(f"Conversa: wa.me/{fone}")

    evo.enviar_texto(telefone_vendedora, "\n".join(linhas))
    eventos.registrar(
        conn, "quente",
        f"{lead['nome']} marcado como quente — aviso enviado à vendedora",
        agora, lead["id"],
    )
```

- [ ] **Step 4: Implementar `resposta.py`**

```python
# src/disparo/resposta.py
from __future__ import annotations

import random
import sqlite3
import time as _time
from datetime import datetime
from typing import Callable

from disparo import blocklist, eventos, handoff, humano
from disparo.conversador import TETO_TURNOS, conversar
from disparo.maquina import Status, robo_pode_falar, status_de, transicionar
from disparo.midia import MensagemNormalizada

_DECISAO_PARA_STATUS = {
    "quente": Status.QUENTE,
    "frio": Status.FRIO,
    "opt_out": Status.OPT_OUT,
    "dado_desatualizado": Status.DADO_DESATUALIZADO,
}


def _lead_por_telefone(conn: sqlite3.Connection, telefone: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM leads WHERE telefone_e164 = ?", (telefone,)
    ).fetchone()


def _historico(conn: sqlite3.Connection, lead_id: int) -> list[dict]:
    linhas = conn.execute(
        "SELECT direcao, texto FROM mensagens WHERE lead_id = ? ORDER BY id",
        (lead_id,),
    ).fetchall()
    return [dict(linha) for linha in linhas]


def processar(conn: sqlite3.Connection, evo, cliente_claude, cfg,
              mensagem: MensagemNormalizada, agora: datetime,
              rng: random.Random,
              dormir: Callable[[float], None] = _time.sleep) -> None:
    """Trata uma mensagem recebida: grava, responde e atualiza o estado do lead."""
    lead = _lead_por_telefone(conn, mensagem.telefone)
    if lead is None:
        return

    if not robo_pode_falar(status_de(conn, lead["id"])):
        return

    cursor = conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, transcricao, "
        "wa_message_id, criado_em) VALUES (?, 'entrada', ?, ?, ?, ?, ?) "
        "ON CONFLICT(wa_message_id) DO NOTHING",
        (lead["id"], mensagem.tipo, mensagem.texto,
         mensagem.texto if mensagem.tipo == "audio" else None,
         mensagem.wa_message_id, agora.isoformat()),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return  # webhook repetido

    if status_de(conn, lead["id"]) == Status.CONTATADO:
        transicionar(conn, lead["id"], Status.EM_CONVERSA, agora)

    dormir(humano.atraso_leitura(rng))
    evo.marcar_lida(mensagem.telefone, mensagem.wa_message_id)

    historico = _historico(conn, lead["id"])
    if mensagem.imagem_b64:
        historico[-1]["imagem_b64"] = mensagem.imagem_b64
        historico[-1]["media_type"] = mensagem.media_type

    qualificacao = conversar(cliente_claude, dict(lead), historico)

    turnos = lead["turnos"] + 1
    decisao = qualificacao.decisao
    if turnos >= TETO_TURNOS and decisao == "continuar":
        decisao = "quente"

    dormir(humano.atraso_resposta(rng))
    for parte in humano.quebrar(qualificacao.resposta):
        evo.digitando(mensagem.telefone, humano.duracao_digitando(parte, rng))
        dormir(humano.duracao_digitando(parte, rng))
        wa_id = evo.enviar_texto(mensagem.telefone, parte)
        conn.execute(
            "INSERT INTO mensagens (lead_id, direcao, tipo, texto, wa_message_id, "
            "criado_em) VALUES (?, 'saida', 'texto', ?, ?, ?)",
            (lead["id"], parte, wa_id, agora.isoformat()),
        )

    conn.execute(
        "UPDATE leads SET turnos = ?, resumo = ?, paga_hoje = ?, tem_cobertura = ?, "
        "carro_quitado = ?, ultimo_evento_em = ? WHERE id = ?",
        (turnos, qualificacao.resumo, qualificacao.paga_hoje,
         qualificacao.tem_cobertura, qualificacao.carro_quitado,
         agora.isoformat(), lead["id"]),
    )
    conn.commit()

    novo_status = _DECISAO_PARA_STATUS.get(decisao)
    if novo_status is None:
        eventos.registrar(
            conn, "resposta", f"{lead['nome']} respondeu", agora, lead["id"]
        )
        return

    transicionar(conn, lead["id"], novo_status, agora)

    if novo_status is Status.OPT_OUT:
        blocklist.bloquear(conn, mensagem.telefone, "opt_out", agora)
        eventos.registrar(
            conn, "alerta",
            f"{lead['nome']} pediu opt-out — número na blocklist",
            agora, lead["id"],
        )
    elif novo_status is Status.QUENTE:
        handoff.avisar_vendedora(
            conn, evo, cfg.vendedora_telefone, lead, qualificacao, agora
        )
    else:
        eventos.registrar(
            conn, "sistema", f"{lead['nome']} encerrado como {novo_status}",
            agora, lead["id"],
        )
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/test_resposta.py -v`
Expected: PASS (6 testes)

- [ ] **Step 6: Commit**

```bash
git add src/disparo/handoff.py src/disparo/resposta.py tests/test_resposta.py
git commit -m "feat: pipeline de resposta ao lead e handoff a vendedora"
```

---

### Task 15: Webhook e aplicação

**Files:**
- Create: `src/disparo/webhook.py`, `src/disparo/app.py`, `tests/test_webhook.py`

**Interfaces:**
- Consumes: `midia.normalizar`, `resposta.processar`, `config.carregar_config`.
- Produces: `webhook.montar_rotas(app, estado)` e `app.criar_app(estado) -> FastAPI`, onde `estado` é um `SimpleNamespace` com `conn`, `evo`, `claude`, `cfg`, `rng`, `transcritor`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_webhook.py
import random
from types import SimpleNamespace

from fastapi.testclient import TestClient

from disparo.app import criar_app


class EvoFalsa:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return "WA-out"

    def marcar_lida(self, telefone, wa_message_id):
        pass

    def digitando(self, telefone, segundos):
        pass


def _estado(conn):
    from disparo.conversador import Qualificacao
    q = Qualificacao(resposta="Tudo bem também.", decisao="continuar", resumo="",
                     paga_hoje=None, tem_cobertura="nao_informado",
                     carro_quitado="nao_informado")
    return SimpleNamespace(
        conn=conn,
        evo=EvoFalsa(),
        claude=SimpleNamespace(messages=SimpleNamespace(
            parse=lambda **kw: SimpleNamespace(parsed_output=q))),
        cfg=SimpleNamespace(vendedora_telefone="5511900000000",
                            painel_senha="segredo"),
        rng=random.Random(1),
        transcritor=lambda b: "",
        dormir=lambda s: None,
    )


def test_webhook_aceita_e_responde_200(conn, lead):
    conn.execute("UPDATE leads SET status = 'contatado' WHERE id = ?", (lead,))
    conn.commit()
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    corpo = {"data": {"key": {"id": "WA-in", "remoteJid": "5511988884444@s.whatsapp.net",
                              "fromMe": False},
                      "message": {"conversation": "tudo bem e vc"}}}
    resposta = cliente.post("/webhook", json=corpo)
    assert resposta.status_code == 200
    assert estado.evo.enviados


def test_webhook_ignora_mensagem_propria(conn, lead):
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    corpo = {"data": {"key": {"id": "WA-x", "remoteJid": "5511988884444@s.whatsapp.net",
                              "fromMe": True},
                      "message": {"conversation": "oi"}}}
    assert cliente.post("/webhook", json=corpo).status_code == 200
    assert estado.evo.enviados == []


def test_webhook_com_corpo_estranho_nao_quebra(conn):
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    assert cliente.post("/webhook", json={"foo": "bar"}).status_code == 200
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_webhook.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.app'`

- [ ] **Step 3: Implementar `webhook.py`**

```python
# src/disparo/webhook.py
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Request

from disparo.midia import normalizar
from disparo.resposta import processar


def criar_rotas(estado) -> APIRouter:
    rotas = APIRouter()

    @rotas.post("/webhook")
    async def receber(request: Request, tarefas: BackgroundTasks) -> dict:
        payload = await request.json()
        mensagem = normalizar(payload, estado.transcritor)
        if mensagem is None or not mensagem.telefone:
            return {"ok": True}
        tarefas.add_task(
            processar, estado.conn, estado.evo, estado.claude, estado.cfg,
            mensagem, datetime.now(), estado.rng, estado.dormir,
        )
        return {"ok": True}

    return rotas
```

- [ ] **Step 4: Implementar `app.py`**

```python
# src/disparo/app.py
from __future__ import annotations

import random
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from disparo import webhook
from disparo.agendador import tentar_disparar
from disparo.config import carregar_config
from disparo.db import conectar, criar_schema
from disparo.evolution import Evolution
from disparo.midia import transcritor_whisper


def criar_app(estado) -> FastAPI:
    app = FastAPI(title="Disparo Porto Sul")
    app.state.estado = estado
    app.include_router(webhook.criar_rotas(estado))

    @app.get("/saude")
    def saude() -> dict:
        return {"ok": True}

    return app


def montar_estado() -> SimpleNamespace:
    import anthropic

    cfg = carregar_config()
    conn = conectar(cfg.db)
    criar_schema(conn)
    return SimpleNamespace(
        cfg=cfg,
        conn=conn,
        evo=Evolution(cfg.evolution_base_url, cfg.evolution_api_key,
                      cfg.evolution_instance, httpx.Client(timeout=30)),
        claude=anthropic.Anthropic(api_key=cfg.anthropic_api_key),
        rng=random.SystemRandom(),
        transcritor=transcritor_whisper(cfg.whisper_modelo),
        dormir=time.sleep,
    )


def main() -> FastAPI:
    estado = montar_estado()
    app = criar_app(estado)

    agenda = BackgroundScheduler(timezone="America/Sao_Paulo")
    agenda.add_job(
        lambda: tentar_disparar(estado.conn, estado.evo, datetime.now(), estado.rng),
        "interval", minutes=1, id="disparo",
    )
    agenda.start()
    return app
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/test_webhook.py -v`
Expected: PASS (3 testes)

- [ ] **Step 6: Commit**

```bash
git add src/disparo/webhook.py src/disparo/app.py tests/test_webhook.py
git commit -m "feat: webhook da Evolution e montagem da aplicacao"
```

---

### Task 16: Painel de monitoramento

**Files:**
- Create: `src/disparo/painel.py`, `src/disparo/static/painel.html`, `tests/test_painel.py`
- Modify: `src/disparo/app.py` (incluir as rotas do painel)

**Interfaces:**
- Consumes: `eventos.listar`, `cota`, `disjuntor`, `importador.importar_csv`.
- Produces: rotas `GET /painel`, `GET /api/estado`, `GET /api/leads`, `GET /api/leads/{id}`, `GET /api/eventos/stream`, `POST /api/pausar`, `POST /api/importar`, todas protegidas por HTTP Basic com a senha de `cfg.painel_senha`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_painel.py
import random
from datetime import date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from disparo.app import criar_app
from disparo.cota import definir_inicio

AUTH = ("operador", "segredo")


def _estado(conn):
    return SimpleNamespace(
        conn=conn, evo=None, claude=None, rng=random.Random(1),
        transcritor=lambda b: "", dormir=lambda s: None,
        cfg=SimpleNamespace(vendedora_telefone="5511900000000",
                            painel_senha="segredo"),
    )


def test_exige_autenticacao(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    assert cliente.get("/api/estado").status_code == 401


def test_senha_errada_e_recusada(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    assert cliente.get("/api/estado", auth=("operador", "errada")).status_code == 401


def test_estado_traz_cota_e_disjuntor(conn):
    definir_inicio(conn, date(2026, 8, 3))
    cliente = TestClient(criar_app(_estado(conn)))
    dados = cliente.get("/api/estado", auth=AUTH).json()
    assert dados["limite"] in (10, 20, 30)
    assert dados["pausado"] is False
    assert "disjuntor" in dados


def test_lista_leads_com_filtro_e_busca(conn, lead):
    cliente = TestClient(criar_app(_estado(conn)))
    todos = cliente.get("/api/leads", auth=AUTH).json()
    assert len(todos) == 1
    assert cliente.get("/api/leads?status=quente", auth=AUTH).json() == []
    assert len(cliente.get("/api/leads?busca=Joao", auth=AUTH).json()) == 1
    assert cliente.get("/api/leads?busca=Zulmira", auth=AUTH).json() == []


def test_conversa_de_um_lead(conn, lead):
    conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, texto, wa_message_id, criado_em) "
        "VALUES (?, 'saida', 'Oii Joao, tudo bem?', 'WA1', ?)",
        (lead, datetime(2026, 8, 4, 9).isoformat()),
    )
    conn.commit()
    cliente = TestClient(criar_app(_estado(conn)))
    dados = cliente.get(f"/api/leads/{lead}", auth=AUTH).json()
    assert dados["lead"]["nome"] == "Joao"
    assert len(dados["mensagens"]) == 1


def test_pausar_e_retomar_pela_api(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    cliente.post("/api/pausar", json={"pausar": True}, auth=AUTH)
    assert cliente.get("/api/estado", auth=AUTH).json()["pausado"] is True
    cliente.post("/api/pausar", json={"pausar": False}, auth=AUTH)
    assert cliente.get("/api/estado", auth=AUTH).json()["pausado"] is False


def test_importar_csv_pela_api(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    csv = b"nome,telefone,veiculo\nAna,11955551111,Kwid 2020\n"
    resposta = cliente.post(
        "/api/importar", files={"arquivo": ("lista.csv", csv, "text/csv")}, auth=AUTH
    )
    assert resposta.json()["importados"] == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_painel.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.painel'`

- [ ] **Step 3: Implementar `painel.py`**

```python
# src/disparo/painel.py
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     UploadFile, status)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from disparo import cota, disjuntor, eventos
from disparo.importador import importar_csv

ESTATICO = Path(__file__).parent / "static"
_seguranca = HTTPBasic()


def criar_rotas(estado) -> APIRouter:
    rotas = APIRouter()

    def autenticar(credenciais: HTTPBasicCredentials = Depends(_seguranca)) -> None:
        if not secrets.compare_digest(credenciais.password, estado.cfg.painel_senha):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "senha incorreta",
                {"WWW-Authenticate": "Basic"},
            )

    @rotas.get("/painel")
    def pagina() -> FileResponse:
        return FileResponse(ESTATICO / "painel.html")

    @rotas.get("/api/estado", dependencies=[Depends(autenticar)])
    def ler_estado() -> dict:
        hoje = datetime.now().date()
        veredito = disjuntor.avaliar(estado.conn)
        return {
            "pausado": disjuntor.esta_pausado(estado.conn),
            "limite": cota.limite_do_dia(estado.conn, hoje),
            "enviados": cota.enviados_no_dia(estado.conn, hoje),
            "disjuntor": {"ok": veredito.ok, "motivo": veredito.motivo},
        }

    @rotas.get("/api/leads", dependencies=[Depends(autenticar)])
    def listar_leads(status_filtro: str | None = Query(None, alias="status"),
                     busca: str | None = None) -> list[dict]:
        sql = "SELECT * FROM leads WHERE 1=1"
        params: list = []
        if status_filtro:
            sql += " AND status = ?"
            params.append(status_filtro)
        if busca:
            sql += " AND (nome LIKE ? OR telefone_e164 LIKE ?)"
            params += [f"%{busca}%", f"%{busca}%"]
        sql += " ORDER BY COALESCE(ultimo_evento_em, criado_em) DESC LIMIT 200"
        return [dict(linha) for linha in estado.conn.execute(sql, params)]

    @rotas.get("/api/leads/{lead_id}", dependencies=[Depends(autenticar)])
    def ler_lead(lead_id: int) -> dict:
        lead = estado.conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if lead is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "lead não encontrado")
        mensagens = estado.conn.execute(
            "SELECT direcao, tipo, texto, criado_em FROM mensagens "
            "WHERE lead_id = ? ORDER BY id",
            (lead_id,),
        ).fetchall()
        return {"lead": dict(lead), "mensagens": [dict(m) for m in mensagens]}

    @rotas.post("/api/pausar", dependencies=[Depends(autenticar)])
    async def alternar_pausa(request: Request) -> dict:
        corpo = await request.json()
        agora = datetime.now()
        if corpo.get("pausar"):
            disjuntor.pausar(estado.conn, "pausa manual pelo painel", agora)
        else:
            disjuntor.retomar(estado.conn, agora)
        return {"pausado": disjuntor.esta_pausado(estado.conn)}

    @rotas.post("/api/importar", dependencies=[Depends(autenticar)])
    async def importar(arquivo: UploadFile = File(...)) -> dict:
        conteudo = (await arquivo.read()).decode("utf-8-sig")
        rel = importar_csv(estado.conn, conteudo, datetime.now())
        eventos.registrar(
            estado.conn, "sistema",
            f"Importação: {rel.importados} novos de {rel.lidos} lidos",
            datetime.now(),
        )
        return rel.__dict__

    @rotas.get("/api/eventos/stream", dependencies=[Depends(autenticar)])
    async def stream() -> StreamingResponse:
        async def gerar():
            ultimo = 0
            while True:
                novos = [
                    e for e in reversed(eventos.listar(estado.conn, 50))
                    if e["id"] > ultimo
                ]
                for evento in novos:
                    ultimo = evento["id"]
                    yield f"data: {json.dumps(evento)}\n\n"
                await asyncio.sleep(3)

        return StreamingResponse(gerar(), media_type="text/event-stream")

    return rotas
```

- [ ] **Step 4: Criar `static/painel.html`**

Copie o protótipo aprovado em `docs/superpowers/specs/2026-08-04-disparo-qualificacao-design.md` (link do artifact) e troque os dados fictícios por chamadas reais:

```html
<script>
  const cabecalho = { credentials: "same-origin" };

  async function carregarEstado() {
    const r = await fetch("/api/estado", cabecalho);
    const e = await r.json();
    document.getElementById("cotaValor").innerHTML =
      `${e.enviados}<small> / ${e.limite}</small>`;
    document.getElementById("cotaBar").style.width =
      `${e.limite ? (e.enviados / e.limite) * 100 : 0}%`;
    document.getElementById("lampText").textContent = e.pausado ? "Pausado" : "Ativo";
    document.getElementById("lamp").dataset.state = e.pausado ? "pausado" : "ativo";
    document.getElementById("breakerState").textContent = e.disjuntor.ok ? "OK" : "PAUSA";
  }

  async function carregarLeads() {
    const busca = document.getElementById("busca").value;
    const filtro = document.getElementById("filtro").value;
    const q = new URLSearchParams();
    if (busca) q.set("busca", busca);
    if (filtro) q.set("status", filtro);
    const leads = await (await fetch(`/api/leads?${q}`, cabecalho)).json();
    renderizarLeads(leads);
  }

  async function abrirLead(id) {
    const dados = await (await fetch(`/api/leads/${id}`, cabecalho)).json();
    renderizarConversa(dados.lead, dados.mensagens);
  }

  new EventSource("/api/eventos/stream").onmessage = (ev) => {
    adicionarEvento(JSON.parse(ev.data));
  };

  document.getElementById("killBtn").addEventListener("click", async () => {
    const pausado = document.getElementById("lamp").dataset.state === "pausado";
    await fetch("/api/pausar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pausar: !pausado }),
    });
    carregarEstado();
  });

  carregarEstado();
  carregarLeads();
  setInterval(carregarEstado, 15000);
</script>
```

As funções `renderizarLeads`, `renderizarConversa` e `adicionarEvento` já existem no protótipo — reaproveite o HTML e o CSS inteiros, trocando apenas o array `leads` fixo pelas chamadas acima e removendo o bloco `.notice` de protótipo.

- [ ] **Step 5: Ligar as rotas em `app.py`**

```python
# em src/disparo/app.py, dentro de criar_app, após include_router(webhook...)
from disparo import painel
app.include_router(painel.criar_rotas(estado))
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `pytest tests/test_painel.py -v`
Expected: PASS (7 testes)

- [ ] **Step 7: Commit**

```bash
git add src/disparo/painel.py src/disparo/static/painel.html src/disparo/app.py tests/test_painel.py
git commit -m "feat: painel de monitoramento com SSE, filtro e importacao"
```

---

### Task 17: Encerramento por inatividade e backup

**Files:**
- Create: `src/disparo/manutencao.py`, `tests/test_manutencao.py`
- Modify: `src/disparo/app.py` (agendar as duas rotinas)

**Interfaces:**
- Consumes: `maquina`, `eventos`.
- Produces: `manutencao.encerrar_sem_resposta(conn, agora: datetime, horas: int = 72) -> int`, `manutencao.backup(conn, destino: Path, agora: datetime) -> Path`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_manutencao.py
from datetime import datetime, timedelta

from disparo.manutencao import backup, encerrar_sem_resposta
from disparo.maquina import Status, status_de

AGORA = datetime(2026, 8, 8, 10, 0)


def _contatar(conn, lead_id, quando):
    conn.execute(
        "UPDATE leads SET status = 'contatado', contatado_em = ? WHERE id = ?",
        (quando.isoformat(), lead_id),
    )
    conn.commit()


def test_encerra_apos_72h(conn, lead):
    _contatar(conn, lead, AGORA - timedelta(hours=73))
    assert encerrar_sem_resposta(conn, AGORA) == 1
    assert status_de(conn, lead) == Status.SEM_RESPOSTA


def test_nao_encerra_antes_de_72h(conn, lead):
    _contatar(conn, lead, AGORA - timedelta(hours=71))
    assert encerrar_sem_resposta(conn, AGORA) == 0
    assert status_de(conn, lead) == Status.CONTATADO


def test_nao_encerra_quem_ja_respondeu(conn, lead):
    _contatar(conn, lead, AGORA - timedelta(hours=100))
    conn.execute("UPDATE leads SET status = 'em_conversa' WHERE id = ?", (lead,))
    conn.commit()
    assert encerrar_sem_resposta(conn, AGORA) == 0


def test_backup_gera_arquivo_legivel(conn, lead, tmp_path):
    caminho = backup(conn, tmp_path, AGORA)
    assert caminho.exists()
    import sqlite3
    copia = sqlite3.connect(caminho)
    total = copia.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    assert total == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_manutencao.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.manutencao'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/manutencao.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from disparo import eventos
from disparo.maquina import Status, transicionar


def encerrar_sem_resposta(conn: sqlite3.Connection, agora: datetime,
                          horas: int = 72) -> int:
    corte = (agora - timedelta(hours=horas)).isoformat()
    linhas = conn.execute(
        "SELECT id, nome FROM leads WHERE status = 'contatado' AND contatado_em < ?",
        (corte,),
    ).fetchall()
    for linha in linhas:
        transicionar(conn, linha["id"], Status.SEM_RESPOSTA, agora)
        eventos.registrar(
            conn, "sistema",
            f"{linha['nome']} encerrado sem resposta após {horas}h",
            agora, linha["id"],
        )
    return len(linhas)


def backup(conn: sqlite3.Connection, destino: Path, agora: datetime) -> Path:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / f"disparo-{agora:%Y%m%d}.db"
    copia = sqlite3.connect(caminho)
    with copia:
        conn.backup(copia)
    copia.close()
    return caminho
```

- [ ] **Step 4: Agendar em `app.py`**

```python
# em src/disparo/app.py, dentro de main(), após o job de disparo
from pathlib import Path

from disparo.manutencao import backup, encerrar_sem_resposta

agenda.add_job(
    lambda: encerrar_sem_resposta(estado.conn, datetime.now()),
    "interval", hours=1, id="sem_resposta",
)
agenda.add_job(
    lambda: backup(estado.conn, Path("./backups"), datetime.now()),
    "cron", hour=3, id="backup",
)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/test_manutencao.py -v`
Expected: PASS (4 testes)

- [ ] **Step 6: Rodar a suíte inteira**

Run: `pytest -v`
Expected: PASS em todos os arquivos de teste

- [ ] **Step 7: Commit**

```bash
git add src/disparo/manutencao.py src/disparo/app.py tests/test_manutencao.py
git commit -m "feat: encerramento por inatividade e backup diario"
```

---

### Task 18: Empacotamento e README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `README.md`

**Interfaces:**
- Consumes: `app.main`.
- Produces: nada de código; entrega o serviço executável no VPS.

- [ ] **Step 1: Criar o `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "disparo.app:main", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Criar o `docker-compose.yml`**

```yaml
services:
  disparo:
    build: .
    restart: unless-stopped
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./dados:/app/dados
      - ./backups:/app/backups
```

A porta fica presa em `127.0.0.1` de propósito: o painel só deve ser alcançado pelo nginx com HTTPS, nunca direto da internet.

- [ ] **Step 3: Criar o `README.md`**

````markdown
# Disparo e qualificação — Porto Sul

Serviço que importa listas CSV, abre conversa no WhatsApp com até 30 pessoas por
dia, qualifica cada lead com o Claude Haiku e entrega os leads quentes para a
vendedora.

Especificação: `docs/superpowers/specs/2026-08-04-disparo-qualificacao-design.md`

## Subir

```bash
cp .env.example .env    # preencha as chaves
docker compose up -d --build
```

## Primeira operação

1. Configure o webhook da Evolution apontando para `http://disparo:8000/webhook`,
   com o evento `messages.upsert` habilitado.
2. Abra o painel em `https://seu-dominio/painel` e faça login com a senha do `.env`.
3. Defina a data de início da operação (isso libera a rampa):

```bash
docker compose exec disparo python -c "
from datetime import date
from disparo.config import carregar_config
from disparo.db import conectar
from disparo.cota import definir_inicio
definir_inicio(conectar(carregar_config().db), date.today())"
```

4. Importe a primeira lista pelo painel.

## Rodar os testes

```bash
pip install -e ".[dev]"
pytest -v
```

## Parar tudo agora

Botão **Pausar tudo** no painel. Nenhum disparo sai enquanto estiver pausado.
````

- [ ] **Step 4: Verificar que a imagem sobe**

Run: `docker compose build`
Expected: build conclui sem erro

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml README.md
git commit -m "chore: empacotamento em Docker e README de operacao"
```

---

## Cobertura da especificação

| Requisito do spec | Tarefa |
|---|---|
| Importador CSV com dedup e blocklist | 2, 3, 4 |
| Rampa 10/20/30 e cota diária | 5 |
| Janela 09:00–18:00 seg–sex | 6 |
| Intervalo 2–8 min entre disparos | 9, 13 |
| Verificação prévia do número | 10, 13 |
| Mensagem de abertura em código, com variações | 12, 13 |
| Webhook idempotente | 1 (UNIQUE), 14, 15 |
| Áudio transcrito local, imagem via visão | 11, 12 |
| Roteiro de 5 etapas e regras de escrita | 12 |
| Classificação e critério de quente | 12, 14 |
| Teto de 12 mensagens | 12, 14 |
| Opt-out com blocklist permanente | 3, 14 |
| Handoff à vendedora | 14 |
| Disjuntor com três limiares | 8, 13 |
| Kill switch | 8, 16 |
| Sem insistência, encerra em 72h | 17 |
| Máquina de estados com `dado_desatualizado` | 7 |
| Painel com filtro, busca, conversa, SSE, autenticação | 16 |
| Backup diário | 17 |
| Tratamento de falhas da Evolution e do Claude | 10, 13, 14 |
