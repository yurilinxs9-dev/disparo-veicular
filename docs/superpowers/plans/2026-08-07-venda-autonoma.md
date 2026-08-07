# Venda Autônoma (Etapa 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A IA fecha a venda sozinha: cota no Power CRM, apresenta preço de tabela, cobra por boleto/PIX e aciona a equipe só para a vistoria.

**Architecture:** Evolução do serviço da Etapa 1. O conversador ganha ferramentas (tool use do Claude); a máquina de estados ganha `negociando`, `aguardando_pagamento`, `pago` e `escalado`; um cliente novo isola a Power API; um webhook novo recebe a confirmação de pagamento. Haiku segue na triagem, Sonnet assume da cotação em diante.

**Tech Stack:** Python 3.12, FastAPI, SQLite, httpx + respx, anthropic (tool use + saída estruturada), pytest. Spec: `docs/superpowers/specs/2026-08-07-venda-autonoma-design.md`.

## Global Constraints

- Nomes de código, mensagens e commits em português, seguindo o estilo da Etapa 1.
- Preço é sempre o devolvido pelo Power CRM. Nenhum caminho de código aplica desconto.
- A IA só gera cobrança depois de aceite explícito do lead (regra fica no prompt; o código exige cotação prévia).
- **Contrato assumido da Power API** (a doc logada ainda não chegou — tudo que depende dele vive em `powercrm.py` e nos fakes):
  - `POST {base}/cotacoes` body `{"nome", "telefone", "placa"}` → `200 {"id", "plano", "mensalidade", "adesao"}`
  - `POST {base}/cotacoes/{id}/cobrancas` body `{}` → `200 {"id", "url_boleto", "pix_copia_cola"}`
  - Header `Authorization: Bearer {token}` em tudo. 4xx = recusa; 5xx/timeout = indisponível.
  - Webhook de pagamento: `POST /webhook/powercrm` body `{"evento": "cobranca_paga", "cobranca_id": "..."}`, header `Authorization: Bearer {POWERCRM_WEBHOOK_TOKEN}`.
  - Quando o token e a doc real chegarem: ajustar SOMENTE `powercrm.py`, o payload do webhook em `pagamento.py` e os fakes correspondentes.
- Todos os testes existentes (123) continuam passando após cada task.

---

### Task 1: Configuração e colunas novas no banco

**Files:**
- Modify: `src/disparo/config.py`
- Modify: `src/disparo/db.py`
- Test: `tests/test_config.py` (adicionar), `tests/test_db.py` (adicionar)

**Interfaces:**
- Consumes: `Config` e `carregar_config(env)` existentes; `criar_schema(conn)` existente.
- Produces: campos novos em `Config`: `powercrm_base_url: str`, `powercrm_token: str`, `powercrm_webhook_token: str`, `equipe_telefone: str` (default = `vendedora_telefone`), `modelo_triagem: str` (default `"claude-haiku-4-5"`), `modelo_fechamento: str` (default `"claude-sonnet-5"`). Em `db.py`: `garantir_colunas(conn)` chamado por `criar_schema`, colunas novas em `leads`: `placa`, `cotacao_id`, `plano`, `mensalidade`, `adesao`, `cobranca_id`, `boleto_url`, `cobranca_enviada_em`, `lembrete_em` (todas TEXT, NULL).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_config.py`:

```python
def _env_completo(**extras):
    base = {
        "DISPARO_DB": "x.db", "ANTHROPIC_API_KEY": "k",
        "EVOLUTION_BASE_URL": "http://e", "EVOLUTION_API_KEY": "k",
        "EVOLUTION_INSTANCE": "i", "VENDEDORA_TELEFONE": "5511900000000",
        "PAINEL_SENHA": "s",
    }
    base.update(extras)
    return base


def test_powercrm_opcional_com_defaults():
    from disparo.config import carregar_config
    cfg = carregar_config(_env_completo())
    assert cfg.powercrm_base_url == ""
    assert cfg.equipe_telefone == "5511900000000"
    assert cfg.modelo_triagem == "claude-haiku-4-5"
    assert cfg.modelo_fechamento == "claude-sonnet-5"


def test_powercrm_configurado():
    from disparo.config import carregar_config
    cfg = carregar_config(_env_completo(
        POWERCRM_BASE_URL="https://api.powercrm.com.br/",
        POWERCRM_TOKEN="t1", POWERCRM_WEBHOOK_TOKEN="t2",
        EQUIPE_TELEFONE="5537999990000",
    ))
    assert cfg.powercrm_base_url == "https://api.powercrm.com.br"
    assert cfg.powercrm_token == "t1"
    assert cfg.powercrm_webhook_token == "t2"
    assert cfg.equipe_telefone == "5537999990000"
```

Adicionar ao fim de `tests/test_db.py`:

```python
def test_colunas_da_etapa_2_existem(conn):
    colunas = {l["name"] for l in conn.execute("PRAGMA table_info(leads)")}
    assert {"placa", "cotacao_id", "plano", "mensalidade", "adesao",
            "cobranca_id", "boleto_url", "cobranca_enviada_em",
            "lembrete_em"} <= colunas


def test_garantir_colunas_e_idempotente(conn):
    from disparo.db import garantir_colunas
    garantir_colunas(conn)
    garantir_colunas(conn)  # segunda chamada não pode explodir
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_config.py tests/test_db.py -v`
Expected: FAIL (`AttributeError: powercrm_base_url` e colunas ausentes)

- [ ] **Step 3: Implementar**

Em `src/disparo/config.py`, adicionar os campos ao dataclass `Config`:

```python
    powercrm_base_url: str
    powercrm_token: str
    powercrm_webhook_token: str
    equipe_telefone: str
    modelo_triagem: str
    modelo_fechamento: str
```

E no `return Config(...)` de `carregar_config`:

```python
        powercrm_base_url=e.get("POWERCRM_BASE_URL", "").rstrip("/"),
        powercrm_token=e.get("POWERCRM_TOKEN", ""),
        powercrm_webhook_token=e.get("POWERCRM_WEBHOOK_TOKEN", ""),
        equipe_telefone=e.get("EQUIPE_TELEFONE") or e["VENDEDORA_TELEFONE"],
        modelo_triagem=e.get("MODELO_TRIAGEM", "claude-haiku-4-5"),
        modelo_fechamento=e.get("MODELO_FECHAMENTO", "claude-sonnet-5"),
```

Em `src/disparo/db.py`, adicionar após `criar_schema`:

```python
_COLUNAS_ETAPA_2 = (
    "placa", "cotacao_id", "plano", "mensalidade", "adesao",
    "cobranca_id", "boleto_url", "cobranca_enviada_em", "lembrete_em",
)


def garantir_colunas(conn: sqlite3.Connection) -> None:
    existentes = {l["name"] for l in conn.execute("PRAGMA table_info(leads)")}
    for coluna in _COLUNAS_ETAPA_2:
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {coluna} TEXT")
    conn.commit()
```

E no fim de `criar_schema`, antes do `commit` final (ou logo após), chamar `garantir_colunas(conn)`.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_config.py tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte inteira e commit**

Run: `pytest -q` — Expected: tudo verde.

```bash
git add src/disparo/config.py src/disparo/db.py tests/test_config.py tests/test_db.py
git commit -m "feat: config do power crm e colunas de fechamento no banco"
```

---

### Task 2: Máquina de estados com os status de fechamento

**Files:**
- Modify: `src/disparo/maquina.py`
- Test: `tests/test_maquina.py` (adicionar)

**Interfaces:**
- Consumes: `Status`, `TRANSICOES`, `TERMINAIS`, `robo_pode_falar` existentes.
- Produces: `Status.NEGOCIANDO = "negociando"`, `Status.AGUARDANDO_PAGAMENTO = "aguardando_pagamento"`, `Status.PAGO = "pago"`, `Status.ESCALADO = "escalado"`. Transições: `EM_CONVERSA → NEGOCIANDO`, `NEGOCIANDO → AGUARDANDO_PAGAMENTO`, `AGUARDANDO_PAGAMENTO → PAGO`; `EM_CONVERSA | NEGOCIANDO | AGUARDANDO_PAGAMENTO → ESCALADO` e também `→ FRIO | OPT_OUT | DADO_DESATUALIZADO | SEM_RESPOSTA`. `PAGO` e `ESCALADO` são terminais. `robo_pode_falar` devolve True também para `NEGOCIANDO` e `AGUARDANDO_PAGAMENTO`. `QUENTE` continua existindo (linhas antigas no banco) mas nenhuma transição nova leva até ele.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_maquina.py`:

```python
def test_fluxo_de_fechamento_completo(conn, lead):
    from disparo.maquina import Status, status_de, transicionar
    from datetime import datetime
    agora = datetime(2026, 8, 7, 10, 0)
    transicionar(conn, lead, Status.CONTATADO, agora)
    transicionar(conn, lead, Status.EM_CONVERSA, agora)
    transicionar(conn, lead, Status.NEGOCIANDO, agora)
    transicionar(conn, lead, Status.AGUARDANDO_PAGAMENTO, agora)
    transicionar(conn, lead, Status.PAGO, agora)
    assert status_de(conn, lead) == Status.PAGO


def test_escalado_de_qualquer_fase_ativa(conn, lead):
    from disparo.maquina import Status, transicionar, TransicaoInvalida
    from datetime import datetime
    import pytest
    agora = datetime(2026, 8, 7, 10, 0)
    transicionar(conn, lead, Status.CONTATADO, agora)
    transicionar(conn, lead, Status.EM_CONVERSA, agora)
    transicionar(conn, lead, Status.NEGOCIANDO, agora)
    transicionar(conn, lead, Status.ESCALADO, agora)
    with pytest.raises(TransicaoInvalida):
        transicionar(conn, lead, Status.NEGOCIANDO, agora)  # terminal


def test_robo_fala_nas_fases_de_fechamento():
    from disparo.maquina import Status, robo_pode_falar
    assert robo_pode_falar(Status.NEGOCIANDO) is True
    assert robo_pode_falar(Status.AGUARDANDO_PAGAMENTO) is True
    assert robo_pode_falar(Status.PAGO) is False
    assert robo_pode_falar(Status.ESCALADO) is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_maquina.py -v`
Expected: FAIL (`AttributeError: NEGOCIANDO`)

- [ ] **Step 3: Implementar**

Em `src/disparo/maquina.py`:

```python
class Status(StrEnum):
    NOVO = "novo"
    CONTATADO = "contatado"
    EM_CONVERSA = "em_conversa"
    NEGOCIANDO = "negociando"
    AGUARDANDO_PAGAMENTO = "aguardando_pagamento"
    PAGO = "pago"
    ESCALADO = "escalado"
    QUENTE = "quente"
    FRIO = "frio"
    OPT_OUT = "opt_out"
    DADO_DESATUALIZADO = "dado_desatualizado"
    SEM_RESPOSTA = "sem_resposta"
    INVALIDO = "invalido"


TERMINAIS = frozenset({
    Status.PAGO, Status.ESCALADO, Status.QUENTE, Status.FRIO, Status.OPT_OUT,
    Status.DADO_DESATUALIZADO, Status.SEM_RESPOSTA, Status.INVALIDO,
})

_FECHAMENTOS = frozenset({
    Status.ESCALADO, Status.QUENTE, Status.FRIO, Status.OPT_OUT,
    Status.DADO_DESATUALIZADO,
})

TRANSICOES: dict[Status, frozenset[Status]] = {
    Status.NOVO: frozenset({Status.CONTATADO, Status.INVALIDO}),
    Status.CONTATADO: frozenset({Status.EM_CONVERSA, Status.SEM_RESPOSTA} | _FECHAMENTOS),
    Status.EM_CONVERSA: frozenset({Status.NEGOCIANDO, Status.SEM_RESPOSTA} | _FECHAMENTOS),
    Status.NEGOCIANDO: frozenset({Status.AGUARDANDO_PAGAMENTO, Status.SEM_RESPOSTA} | _FECHAMENTOS),
    Status.AGUARDANDO_PAGAMENTO: frozenset({Status.PAGO, Status.SEM_RESPOSTA} | _FECHAMENTOS),
}
```

E `robo_pode_falar`:

```python
def robo_pode_falar(status: Status) -> bool:
    return status in (Status.CONTATADO, Status.EM_CONVERSA,
                      Status.NEGOCIANDO, Status.AGUARDANDO_PAGAMENTO)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_maquina.py -v` — Expected: PASS.
Run: `pytest -q` — a suíte inteira precisa continuar verde (o `quente` antigo segue aceito a partir de `CONTATADO`/`EM_CONVERSA` via `_FECHAMENTOS`, então `test_resposta.py` não quebra).

- [ ] **Step 5: Commit**

```bash
git add src/disparo/maquina.py tests/test_maquina.py
git commit -m "feat: estados de negociacao, cobranca, pago e escalado"
```

---

### Task 3: Cliente da Power API

**Files:**
- Create: `src/disparo/powercrm.py`
- Test: `tests/test_powercrm.py`

**Interfaces:**
- Consumes: nada interno; `httpx.Client` injetado (padrão do `evolution.py`).
- Produces:
  - `Cotacao(cotacao_id: str, plano: str, mensalidade: str, adesao: str)` (dataclass frozen)
  - `Cobranca(cobranca_id: str, url_boleto: str, pix_copia_cola: str)` (dataclass frozen)
  - `PowerCRM(base_url, token, http).cotar(nome, telefone, placa) -> Cotacao`
  - `PowerCRM.gerar_cobranca(cotacao_id) -> Cobranca`
  - Exceções: `PowerCRMErro(RuntimeError)` ← `PowerCRMRecusa(status, detalhe)` (4xx) e `PowerCRMIndisponivel` (5xx, timeout, rede).

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_powercrm.py
import httpx
import pytest
import respx

from disparo.powercrm import (Cobranca, Cotacao, PowerCRM, PowerCRMIndisponivel,
                              PowerCRMRecusa)

BASE = "https://api.powercrm.test"


def _cliente():
    return PowerCRM(BASE, "tok", httpx.Client())


@respx.mock
def test_cotar_devolve_cotacao():
    rota = respx.post(f"{BASE}/cotacoes").respond(200, json={
        "id": "C1", "plano": "Master", "mensalidade": "189.90", "adesao": "250.00",
    })
    c = _cliente().cotar("Joao", "5537988884444", "ABC1D23")
    assert c == Cotacao("C1", "Master", "189.90", "250.00")
    corpo = rota.calls.last.request
    assert corpo.headers["Authorization"] == "Bearer tok"


@respx.mock
def test_gerar_cobranca():
    respx.post(f"{BASE}/cotacoes/C1/cobrancas").respond(200, json={
        "id": "B1", "url_boleto": "https://p/b1", "pix_copia_cola": "000201...",
    })
    b = _cliente().gerar_cobranca("C1")
    assert b == Cobranca("B1", "https://p/b1", "000201...")


@respx.mock
def test_4xx_vira_recusa():
    respx.post(f"{BASE}/cotacoes").respond(422, json={"detail": "placa invalida"})
    with pytest.raises(PowerCRMRecusa) as e:
        _cliente().cotar("Joao", "5537988884444", "XXX")
    assert e.value.status == 422


@respx.mock
def test_5xx_vira_indisponivel():
    respx.post(f"{BASE}/cotacoes").respond(503)
    with pytest.raises(PowerCRMIndisponivel):
        _cliente().cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_timeout_vira_indisponivel():
    respx.post(f"{BASE}/cotacoes").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(PowerCRMIndisponivel):
        _cliente().cotar("Joao", "5537988884444", "ABC1D23")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_powercrm.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.powercrm'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/powercrm.py
from __future__ import annotations

from dataclasses import dataclass

import httpx


class PowerCRMErro(RuntimeError):
    pass


class PowerCRMIndisponivel(PowerCRMErro):
    pass


class PowerCRMRecusa(PowerCRMErro):
    def __init__(self, status: int, detalhe: str) -> None:
        super().__init__(f"{status}: {detalhe}")
        self.status = status


@dataclass(frozen=True)
class Cotacao:
    cotacao_id: str
    plano: str
    mensalidade: str
    adesao: str


@dataclass(frozen=True)
class Cobranca:
    cobranca_id: str
    url_boleto: str
    pix_copia_cola: str


class PowerCRM:
    """Contrato assumido da Power API — ajustar aqui quando a doc real chegar."""

    def __init__(self, base_url: str, token: str, http: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._cabecalhos = {"Authorization": f"Bearer {token}"}

    def _post(self, caminho: str, corpo: dict) -> dict:
        try:
            resposta = self._http.post(
                f"{self._base}{caminho}", json=corpo, headers=self._cabecalhos,
            )
        except httpx.HTTPError as erro:
            raise PowerCRMIndisponivel(str(erro)) from erro
        if resposta.status_code >= 500:
            raise PowerCRMIndisponivel(f"HTTP {resposta.status_code}")
        if resposta.status_code >= 400:
            raise PowerCRMRecusa(resposta.status_code, resposta.text)
        return resposta.json()

    def cotar(self, nome: str, telefone: str, placa: str) -> Cotacao:
        dados = self._post("/cotacoes", {
            "nome": nome, "telefone": telefone, "placa": placa,
        })
        return Cotacao(str(dados["id"]), dados["plano"],
                       str(dados["mensalidade"]), str(dados["adesao"]))

    def gerar_cobranca(self, cotacao_id: str) -> Cobranca:
        dados = self._post(f"/cotacoes/{cotacao_id}/cobrancas", {})
        return Cobranca(str(dados["id"]), dados["url_boleto"],
                        dados["pix_copia_cola"])
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_powercrm.py -v` — Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/powercrm.py tests/test_powercrm.py
git commit -m "feat: cliente da power api com cotacao e cobranca"
```

---

### Task 4: Ferramentas do conversador

**Files:**
- Create: `src/disparo/ferramentas.py`
- Test: `tests/test_ferramentas.py`

**Interfaces:**
- Consumes: `PowerCRM` (Task 3), `maquina.transicionar/Status` (Task 2), `eventos.registrar`, colunas novas de `leads` (Task 1).
- Produces:
  - `FERRAMENTAS_SPEC: list[dict]` — as três ferramentas no formato de tools da API do Claude (`cotar`, `gerar_cobranca`, `escalar_humano`).
  - `Ferramentas(conn, powercrm, lead_id, agora)` com `executar(nome: str, entrada: dict) -> str` (o retorno é o texto devolvido ao modelo como tool_result). Efeitos:
    - `cotar(placa)`: chama a API, grava `placa/cotacao_id/plano/mensalidade/adesao` no lead, transiciona para `NEGOCIANDO` (se ainda `EM_CONVERSA`), devolve `"plano {plano}: mensalidade R$ {mensalidade}, adesao R$ {adesao}"`. `PowerCRMIndisponivel` → devolve `"erro: sistema de cotacao fora do ar"` e registra evento `alerta` (não levanta).
    - `gerar_cobranca()`: exige `cotacao_id` gravado (senão devolve `"erro: nenhuma cotacao feita"`), chama a API, grava `cobranca_id/boleto_url/cobranca_enviada_em`, transiciona para `AGUARDANDO_PAGAMENTO`, devolve `"cobranca criada: {url_boleto} | pix: {pix_copia_cola}"`.
    - `escalar_humano(motivo)`: transiciona para `ESCALADO`, registra evento `alerta`, devolve `"escalado"`.
  - `Ferramentas.escalou: bool` e `Ferramentas.falhas_powercrm: int` (contador para a regra de 2 falhas → escalar, usada na Task 6).

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_ferramentas.py
from datetime import datetime

import pytest

from disparo.ferramentas import FERRAMENTAS_SPEC, Ferramentas
from disparo.maquina import Status, status_de, transicionar
from disparo.powercrm import Cobranca, Cotacao, PowerCRMIndisponivel

AGORA = datetime(2026, 8, 7, 11, 0)


class PowerFalso:
    def __init__(self, fora_do_ar=False):
        self.fora_do_ar = fora_do_ar

    def cotar(self, nome, telefone, placa):
        if self.fora_do_ar:
            raise PowerCRMIndisponivel("503")
        return Cotacao("C1", "Master", "189.90", "250.00")

    def gerar_cobranca(self, cotacao_id):
        return Cobranca("B1", "https://p/b1", "000201x")


def _em_conversa(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)


def test_spec_tem_as_tres_ferramentas():
    assert {f["name"] for f in FERRAMENTAS_SPEC} == {
        "cotar", "gerar_cobranca", "escalar_humano"}


def test_cotar_grava_e_negocia(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    saida = f.executar("cotar", {"placa": "ABC1D23"})
    assert "189.90" in saida
    linha = conn.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
    assert linha["cotacao_id"] == "C1"
    assert linha["placa"] == "ABC1D23"
    assert status_de(conn, lead) == Status.NEGOCIANDO


def test_cotar_fora_do_ar_nao_explode(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(fora_do_ar=True), lead, AGORA)
    saida = f.executar("cotar", {"placa": "ABC1D23"})
    assert saida.startswith("erro:")
    assert f.falhas_powercrm == 1
    assert status_de(conn, lead) == Status.EM_CONVERSA


def test_cobranca_exige_cotacao(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    assert f.executar("gerar_cobranca", {}).startswith("erro:")


def test_cobranca_grava_e_aguarda(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    f.executar("cotar", {"placa": "ABC1D23"})
    saida = f.executar("gerar_cobranca", {})
    assert "https://p/b1" in saida
    linha = conn.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
    assert linha["cobranca_id"] == "B1"
    assert linha["cobranca_enviada_em"] == AGORA.isoformat()
    assert status_de(conn, lead) == Status.AGUARDANDO_PAGAMENTO


def test_escalar(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    f.executar("escalar_humano", {"motivo": "pediu desconto"})
    assert f.escalou is True
    assert status_de(conn, lead) == Status.ESCALADO
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_ferramentas.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.ferramentas'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/ferramentas.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from disparo import eventos
from disparo.maquina import Status, status_de, transicionar
from disparo.powercrm import PowerCRMErro

FERRAMENTAS_SPEC = [
    {
        "name": "cotar",
        "description": "Consulta o preço da proteção no Power CRM pela placa. "
                       "Use assim que tiver a placa do veículo.",
        "input_schema": {
            "type": "object",
            "properties": {"placa": {"type": "string"}},
            "required": ["placa"],
        },
    },
    {
        "name": "gerar_cobranca",
        "description": "Gera o boleto (pagável por PIX) da adesão. Use SOMENTE "
                       "depois de o cliente aceitar explicitamente o valor.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "escalar_humano",
        "description": "Passa a conversa para a equipe humana. Use em recusa "
                       "firme, pedido de desconto, ou assunto fora do escopo.",
        "input_schema": {
            "type": "object",
            "properties": {"motivo": {"type": "string"}},
            "required": ["motivo"],
        },
    },
]


class Ferramentas:
    def __init__(self, conn: sqlite3.Connection, powercrm, lead_id: int,
                 agora: datetime) -> None:
        self._conn = conn
        self._power = powercrm
        self._lead = lead_id
        self._agora = agora
        self.escalou = False
        self.falhas_powercrm = 0

    def executar(self, nome: str, entrada: dict) -> str:
        if nome == "cotar":
            return self._cotar(entrada.get("placa", ""))
        if nome == "gerar_cobranca":
            return self._gerar_cobranca()
        if nome == "escalar_humano":
            return self._escalar(entrada.get("motivo", "sem motivo"))
        return f"erro: ferramenta desconhecida {nome}"

    def _linha(self) -> sqlite3.Row:
        return self._conn.execute(
            "SELECT * FROM leads WHERE id = ?", (self._lead,)).fetchone()

    def _cotar(self, placa: str) -> str:
        lead = self._linha()
        try:
            cot = self._power.cotar(lead["nome"], lead["telefone_e164"], placa)
        except PowerCRMErro as erro:
            self.falhas_powercrm += 1
            eventos.registrar(self._conn, "alerta",
                              f"Power CRM falhou na cotação: {erro}",
                              self._agora, self._lead)
            return "erro: sistema de cotacao fora do ar"
        self._conn.execute(
            "UPDATE leads SET placa = ?, cotacao_id = ?, plano = ?, "
            "mensalidade = ?, adesao = ? WHERE id = ?",
            (placa, cot.cotacao_id, cot.plano, cot.mensalidade, cot.adesao,
             self._lead),
        )
        self._conn.commit()
        if status_de(self._conn, self._lead) == Status.EM_CONVERSA:
            transicionar(self._conn, self._lead, Status.NEGOCIANDO, self._agora)
        eventos.registrar(self._conn, "sistema",
                          f"Cotação {cot.plano}: R$ {cot.mensalidade}/mês",
                          self._agora, self._lead)
        return (f"plano {cot.plano}: mensalidade R$ {cot.mensalidade}, "
                f"adesao R$ {cot.adesao}")

    def _gerar_cobranca(self) -> str:
        lead = self._linha()
        if not lead["cotacao_id"]:
            return "erro: nenhuma cotacao feita"
        try:
            cob = self._power.gerar_cobranca(lead["cotacao_id"])
        except PowerCRMErro as erro:
            self.falhas_powercrm += 1
            eventos.registrar(self._conn, "alerta",
                              f"Power CRM falhou na cobrança: {erro}",
                              self._agora, self._lead)
            return "erro: sistema de cobranca fora do ar"
        self._conn.execute(
            "UPDATE leads SET cobranca_id = ?, boleto_url = ?, "
            "cobranca_enviada_em = ? WHERE id = ?",
            (cob.cobranca_id, cob.url_boleto, self._agora.isoformat(),
             self._lead),
        )
        self._conn.commit()
        transicionar(self._conn, self._lead, Status.AGUARDANDO_PAGAMENTO,
                     self._agora)
        eventos.registrar(self._conn, "sistema", "Boleto enviado",
                          self._agora, self._lead)
        return f"cobranca criada: {cob.url_boleto} | pix: {cob.pix_copia_cola}"

    def _escalar(self, motivo: str) -> str:
        transicionar(self._conn, self._lead, Status.ESCALADO, self._agora)
        eventos.registrar(self._conn, "alerta",
                          f"Escalado para humano: {motivo}",
                          self._agora, self._lead)
        self.escalou = True
        return "escalado"
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_ferramentas.py -v` — Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/ferramentas.py tests/test_ferramentas.py
git commit -m "feat: ferramentas de cotacao, cobranca e escalada"
```

---

### Task 5: Conversador com laço de tool use e prompt de fechamento

**Files:**
- Modify: `src/disparo/conversador.py`
- Test: `tests/test_conversador.py` (adicionar)

**Interfaces:**
- Consumes: `FERRAMENTAS_SPEC` e `Ferramentas.executar` (Task 4).
- Produces:
  - `Qualificacao.decisao` vira `Literal["continuar", "frio", "opt_out", "dado_desatualizado", "escalar"]` — **`quente` sai do schema** (esquentar agora é chamar `cotar`, não encerrar).
  - `conversar(cliente, lead, historico, ferramentas=None, modelo=MODELO) -> Qualificacao` — assinatura ganha os dois parâmetros opcionais; chamadas da Etapa 1 (`conversar(cliente, lead, historico)`) continuam funcionando.
  - `TETO_TURNOS = 20`.
  - Laço: enquanto a resposta contiver blocos `tool_use`, executa cada um via `ferramentas.executar(nome, input)`, devolve `tool_result` e chama o modelo de novo; máximo 4 iterações, depois força a saída estruturada.
  - Prompt: seção nova "Fase de fechamento" com o roteiro da spec (pedir placa; apresentar mensalidade + adesão sem floreio; máximo 2 contornos de objeção; nunca desconto — pedido de desconto → `escalar_humano`; aceite explícito antes de `gerar_cobranca`; após enviar boleto, instruir PIX e silenciar).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_conversador.py`:

```python
def test_decisao_quente_saiu_do_schema():
    from disparo.conversador import Qualificacao
    import pytest
    with pytest.raises(Exception):
        Qualificacao(resposta="x", decisao="quente", resumo="r")


def test_laco_executa_ferramenta_e_volta():
    from types import SimpleNamespace
    from disparo.conversador import Qualificacao, conversar

    chamadas = []

    class FerramentasFalsas:
        escalou = False
        falhas_powercrm = 0

        def executar(self, nome, entrada):
            chamadas.append((nome, entrada))
            return "plano Master: mensalidade R$ 189.90, adesao R$ 250.00"

    q = Qualificacao(resposta="Fica R$ 189,90 por mês.", decisao="continuar",
                     resumo="cotado")
    respostas = iter([
        SimpleNamespace(  # 1a chamada: modelo pede a ferramenta
            parsed_output=None,
            content=[SimpleNamespace(type="tool_use", id="t1", name="cotar",
                                     input={"placa": "ABC1D23"})],
        ),
        SimpleNamespace(parsed_output=q, content=[]),  # 2a: saída final
    ])
    cliente = SimpleNamespace(messages=SimpleNamespace(
        parse=lambda **kw: next(respostas)))
    lead = {"nome": "Joao", "veiculo": "Onix 2019"}
    resultado = conversar(cliente, lead, [
        {"direcao": "entrada", "texto": "pode cotar, placa ABC1D23"},
    ], ferramentas=FerramentasFalsas(), modelo="claude-sonnet-5")
    assert chamadas == [("cotar", {"placa": "ABC1D23"})]
    assert resultado.decisao == "continuar"
    assert "189,90" in resultado.resposta


def test_sem_ferramentas_nao_manda_tools():
    from types import SimpleNamespace
    from disparo.conversador import Qualificacao, conversar
    capturado = {}

    def parse(**kw):
        capturado.update(kw)
        return SimpleNamespace(parsed_output=Qualificacao(
            resposta="oi", decisao="continuar", resumo="r"), content=[])

    cliente = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    conversar(cliente, {"nome": "J", "veiculo": "Onix"}, 
              [{"direcao": "entrada", "texto": "oi"}])
    assert "tools" not in capturado or not capturado["tools"]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_conversador.py -v`
Expected: FAIL (`quente` ainda aceito; `conversar` não aceita `ferramentas=`)

- [ ] **Step 3: Implementar**

Em `src/disparo/conversador.py`:

1. `TETO_TURNOS = 20`.
2. `Qualificacao.decisao: Literal["continuar", "frio", "opt_out", "dado_desatualizado", "escalar"]`.
3. Acrescentar ao `PROMPT` (substituindo o parágrafo de abertura "Você não vende..." por "Você conduz da primeira mensagem até o fechamento." e a seção de classificação):

```text
# Fase de fechamento (quando o lead aceita a cotação)
Etapa 6 — peça a placa do veículo para puxar o valor exato.
Etapa 7 — com a placa em mãos, use a ferramenta cotar. Apresente o resultado
  direto: mensalidade e adesão, sem floreio. Não invente nada além do que a
  ferramenta devolver.
Etapa 8 — objeção: contorne no máximo 2 vezes, com argumento (custo de ficar
  sem proteção, aceitação de perfil que seguradora recusa). NUNCA ofereça
  desconto; se o lead insistir em desconto, use escalar_humano.
Etapa 9 — aceite: só use gerar_cobranca depois de um sim explícito ("fecho",
  "pode mandar", "aceito"). Envie o link do boleto e diga que dá pra pagar
  pelo PIX no próprio boleto. Depois disso, encerre educadamente e aguarde.

# Classificação
decisao=frio quando responde sem interesse ou já está satisfeito com o que tem.
decisao=opt_out em qualquer pedido para parar de receber mensagem.
decisao=dado_desatualizado quando o veículo não é dele.
decisao=escalar quando você usou escalar_humano ou a conversa precisa de gente.
decisao=continuar no resto — inclusive durante toda a fase de fechamento.
```

4. `conversar` com laço:

```python
def conversar(cliente: Any, lead: dict, historico: list[dict],
              ferramentas: Any = None, modelo: str = MODELO) -> Qualificacao:
    """Chama o modelo; executa ferramentas até sair a resposta estruturada."""
    sistema = PROMPT.replace("{veiculo}", lead.get("veiculo") or "seu carro")
    mensagens = [
        {
            "role": "assistant" if m["direcao"] == "saida" else "user",
            "content": _conteudo(m),
        }
        for m in historico
    ]
    extras: dict[str, Any] = {}
    if ferramentas is not None:
        extras["tools"] = FERRAMENTAS_SPEC

    for _ in range(4):
        resposta = cliente.messages.parse(
            model=modelo,
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": sistema,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=mensagens,
            output_format=Qualificacao,
            **extras,
        )
        usos = [b for b in getattr(resposta, "content", [])
                if getattr(b, "type", "") == "tool_use"]
        if not usos or ferramentas is None:
            break
        mensagens.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": u.id, "name": u.name, "input": u.input}
            for u in usos
        ]})
        mensagens.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": u.id,
             "content": ferramentas.executar(u.name, dict(u.input))}
            for u in usos
        ]})

    if resposta.parsed_output is None:
        return Qualificacao(resposta="", decisao="escalar",
                            resumo="modelo não devolveu saída estruturada")
    return resposta.parsed_output
```

Import no topo: `from disparo.ferramentas import FERRAMENTAS_SPEC`.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_conversador.py -v` — Expected: PASS.
Run: `pytest -q` — `test_resposta.py` vai QUEBRAR (usa `decisao="quente"`). É esperado: a Task 6 conserta. Se preferir suíte verde por task, troque nos testes antigos `_q("quente", ...)` por `_q("escalar", ...)` já nesta task e ajuste o assert de status para `Status.ESCALADO` — mas o mapeamento em `resposta.py` só muda na Task 6, então o caminho recomendado é fazer Task 5 e 6 no mesmo PR e commitar juntas se a suíte precisar ficar verde a cada commit. Ordem dos commits abaixo assume Task 6 na sequência imediata.

- [ ] **Step 5: Commit (junto com a Task 6 se a suíte estiver vermelha)**

```bash
git add src/disparo/conversador.py tests/test_conversador.py
git commit -m "feat: conversador com ferramentas e roteiro de fechamento"
```

---

### Task 6: Pipeline de resposta — decisões novas, modelo por fase, kill switch

**Files:**
- Modify: `src/disparo/resposta.py`
- Modify: `src/disparo/handoff.py`
- Test: `tests/test_resposta.py` (ajustar + adicionar)

**Interfaces:**
- Consumes: `conversar(..., ferramentas=, modelo=)` (Task 5), `Ferramentas` (Task 4), `Status` novos (Task 2), `cfg.modelo_triagem/modelo_fechamento/equipe_telefone` (Task 1), `disjuntor.esta_pausado`.
- Produces:
  - `processar(conn, evo, cliente_claude, cfg, mensagem, agora, rng, dormir=..., powercrm=None)` — parâmetro novo `powercrm` (cliente da Task 3 ou fake; `None` desliga as ferramentas e o fluxo vira o da Etapa 1).
  - `_DECISAO_PARA_STATUS` = `{"frio": FRIO, "opt_out": OPT_OUT, "dado_desatualizado": DADO_DESATUALIZADO, "escalar": ESCALADO}`.
  - Escolha de modelo: `NEGOCIANDO`/`AGUARDANDO_PAGAMENTO` → `cfg.modelo_fechamento`; resto → `cfg.modelo_triagem`.
  - Kill switch: `disjuntor.esta_pausado(conn)` → grava a mensagem recebida e retorna sem responder.
  - `Ferramentas.falhas_powercrm >= 2` no turno → transiciona `ESCALADO` + `handoff.avisar_escalada`.
  - `handoff.avisar_escalada(conn, evo, telefone_equipe, lead, motivo, agora)` — aviso à equipe quando a IA escala.
  - Teto: `turnos >= TETO_TURNOS` e decisão `continuar` → vira `escalar` (era `quente`).

- [ ] **Step 1: Ajustar os testes antigos e escrever os novos**

Em `tests/test_resposta.py`: no helper `_q`, `decisao` default continua `"continuar"`; substituir o teste `test_lead_quente_avisa_a_vendedora_e_silencia` por:

```python
def test_escalar_avisa_a_equipe_e_silencia(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q("escalar", "Vou te passar pra equipe.")),
              CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    destinos = [d for d, _ in evo.enviados]
    assert "5511900000000" in destinos
    assert status_de(conn, lead) == Status.ESCALADO
```

`CFG` no topo vira:

```python
CFG = SimpleNamespace(vendedora_telefone="5511900000000",
                      equipe_telefone="5511900000000",
                      modelo_triagem="claude-haiku-4-5",
                      modelo_fechamento="claude-sonnet-5")
```

No mesmo passo, atualizar o `_estado` de `tests/test_webhook.py` (o webhook chama `processar`, que agora lê os campos novos do cfg):

```python
        cfg=SimpleNamespace(vendedora_telefone="5511900000000",
                            equipe_telefone="5511900000000",
                            painel_senha="segredo",
                            modelo_triagem="claude-haiku-4-5",
                            modelo_fechamento="claude-sonnet-5"),
```

Adicionar:

```python
def test_pausado_grava_mas_nao_responde(conn, lead):
    from disparo.disjuntor import pausar
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    pausar(conn, "teste", AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None)
    assert evo.enviados == []
    total = conn.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0]
    assert total == 1  # a entrada foi gravada


def test_fase_de_fechamento_usa_sonnet(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.NEGOCIANDO, AGORA)
    modelos = []

    def parse(**kw):
        modelos.append(kw["model"])
        from types import SimpleNamespace
        return SimpleNamespace(parsed_output=_q(), content=[])

    cliente = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    processar(conn, EvoFalsa(), cliente, CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None)
    assert modelos == ["claude-sonnet-5"]


def test_duas_falhas_do_powercrm_escalam(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)

    class PowerQuebrado:
        def cotar(self, *a):
            from disparo.powercrm import PowerCRMIndisponivel
            raise PowerCRMIndisponivel("503")

        def gerar_cobranca(self, *a):
            from disparo.powercrm import PowerCRMIndisponivel
            raise PowerCRMIndisponivel("503")

    from types import SimpleNamespace as NS
    respostas = iter([
        NS(parsed_output=None, content=[
            NS(type="tool_use", id="t1", name="cotar", input={"placa": "A"})]),
        NS(parsed_output=None, content=[
            NS(type="tool_use", id="t2", name="cotar", input={"placa": "A"})]),
        NS(parsed_output=_q(), content=[]),
    ])
    cliente = NS(messages=NS(parse=lambda **kw: next(respostas)))
    evo = EvoFalsa()
    processar(conn, evo, cliente, CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None, powercrm=PowerQuebrado())
    assert status_de(conn, lead) == Status.ESCALADO
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_resposta.py -v`
Expected: FAIL (mapeamento antigo, sem `powercrm=`, sem kill switch)

- [ ] **Step 3: Implementar**

Em `src/disparo/resposta.py`:

```python
from disparo import blocklist, disjuntor, eventos, handoff, humano
from disparo.ferramentas import Ferramentas

_DECISAO_PARA_STATUS = {
    "frio": Status.FRIO,
    "opt_out": Status.OPT_OUT,
    "dado_desatualizado": Status.DADO_DESATUALIZADO,
    "escalar": Status.ESCALADO,
}

_FASES_DE_FECHAMENTO = (Status.NEGOCIANDO, Status.AGUARDANDO_PAGAMENTO)
```

Assinatura e corpo de `processar` (mudanças em relação à Etapa 1 comentadas):

```python
def processar(conn, evo, cliente_claude, cfg, mensagem, agora, rng,
              dormir=_time.sleep, powercrm=None) -> None:
    lead = _lead_por_telefone(conn, mensagem.telefone)
    if lead is None:
        return
    if not robo_pode_falar(status_de(conn, lead["id"])):
        return

    # (gravação idempotente da mensagem: idêntica à Etapa 1)

    if disjuntor.esta_pausado(conn):
        return  # kill switch: mensagem gravada, robô mudo

    # (alerta de transcrição e transição CONTATADO→EM_CONVERSA: idênticos)

    status_atual = status_de(conn, lead["id"])
    modelo = (cfg.modelo_fechamento if status_atual in _FASES_DE_FECHAMENTO
              else cfg.modelo_triagem)
    ferramentas = (Ferramentas(conn, powercrm, lead["id"], agora)
                   if powercrm is not None else None)

    # (atraso de leitura e marcar_lida: idênticos)

    qualificacao = conversar(cliente_claude, dict(lead), historico,
                             ferramentas=ferramentas, modelo=modelo)

    turnos = lead["turnos"] + 1
    decisao = qualificacao.decisao
    if turnos >= TETO_TURNOS and decisao == "continuar":
        decisao = "escalar"
    if ferramentas is not None and ferramentas.falhas_powercrm >= 2:
        decisao = "escalar"
    if ferramentas is not None and ferramentas.escalou:
        decisao = "escalar"

    # (envio humanizado das partes e UPDATE do lead: idênticos à Etapa 1)

    novo_status = _DECISAO_PARA_STATUS.get(decisao)
    if novo_status is None:
        eventos.registrar(conn, "resposta", f"{lead['nome']} respondeu",
                          agora, lead["id"])
        return

    if status_de(conn, lead["id"]) != novo_status:
        transicionar(conn, lead["id"], novo_status, agora)

    if novo_status is Status.OPT_OUT:
        # (idêntico à Etapa 1)
        ...
    elif novo_status is Status.ESCALADO:
        handoff.avisar_escalada(conn, evo, cfg.equipe_telefone, lead,
                                qualificacao.resumo, agora)
    else:
        # (idêntico à Etapa 1)
        ...
```

Nota: quando `qualificacao.resposta` vier vazia (saída de segurança da Task 5), pular o envio das partes e ir direto ao tratamento da decisão.

Em `src/disparo/handoff.py`, adicionar:

```python
def avisar_escalada(conn: sqlite3.Connection, evo, telefone_equipe: str,
                    lead: sqlite3.Row, motivo: str, agora: datetime) -> None:
    fone = lead["telefone_e164"]
    texto = "\n".join([
        f"Assumir conversa — {lead['nome']}",
        f"{lead['veiculo']}",
        motivo or "a IA escalou a conversa",
        f"Conversa: wa.me/{fone}",
    ])
    evo.enviar_texto(telefone_equipe, texto)
    eventos.registrar(conn, "alerta",
                      f"{lead['nome']} escalado para a equipe",
                      agora, lead["id"])
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest -q` — Expected: suíte inteira verde (incluindo os testes ajustados).

- [ ] **Step 5: Commit**

```bash
git add src/disparo/resposta.py src/disparo/handoff.py tests/test_resposta.py
git commit -m "feat: resposta com fechamento, modelo por fase e kill switch total"
```

---

### Task 7: Webhook de pagamento e aviso de vistoria

**Files:**
- Create: `src/disparo/pagamento.py`
- Modify: `src/disparo/app.py` (incluir rotas), `src/disparo/handoff.py` (aviso de vistoria)
- Test: `tests/test_pagamento.py`

**Interfaces:**
- Consumes: `Status` (Task 2), `cfg.powercrm_webhook_token` e `cfg.equipe_telefone` (Task 1), `eventos`.
- Produces:
  - `pagamento.criar_rotas(estado) -> APIRouter` com `POST /webhook/powercrm`: exige header `Authorization: Bearer {cfg.powercrm_webhook_token}` (401 sem ele); evento `cobranca_paga` acha o lead por `cobranca_id`, transiciona para `PAGO`, manda boas-vindas ao cliente e `handoff.avisar_vistoria` à equipe. Idempotente: lead já `PAGO` ou cobrança desconhecida → `{"ok": true}` sem efeito.
  - `handoff.avisar_vistoria(conn, evo, telefone_equipe, lead, agora) -> None`.
  - `BOAS_VINDAS = "Pagamento confirmado, {nome}! Seja bem-vindo à Porto Sul. A equipe já vai te chamar pra agendar a vistoria do veículo."`

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_pagamento.py
import random
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from disparo.app import criar_app
from disparo.maquina import Status, status_de, transicionar

AGORA = datetime(2026, 8, 7, 12, 0)
CABECALHO = {"Authorization": "Bearer whk"}


class EvoFalsa:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return "WA-out"

    def marcar_lida(self, *a):
        pass

    def digitando(self, *a):
        pass


def _estado(conn):
    return SimpleNamespace(
        conn=conn, evo=EvoFalsa(), claude=None, rng=random.Random(1),
        transcritor=lambda b: "", dormir=lambda s: None, powercrm=None,
        cfg=SimpleNamespace(vendedora_telefone="5511900000000",
                            equipe_telefone="5537999990000",
                            painel_senha="segredo",
                            powercrm_webhook_token="whk"),
    )


def _aguardando(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.NEGOCIANDO, AGORA)
    transicionar(conn, lead, Status.AGUARDANDO_PAGAMENTO, AGORA)
    conn.execute("UPDATE leads SET cobranca_id = 'B1' WHERE id = ?", (lead,))
    conn.commit()


def test_sem_token_e_401(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm",
                     json={"evento": "cobranca_paga", "cobranca_id": "B1"})
    assert r.status_code == 401


def test_pagamento_confirma_avisa_cliente_e_equipe(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json={"evento": "cobranca_paga", "cobranca_id": "B1"})
    assert r.status_code == 200
    assert status_de(conn, lead) == Status.PAGO
    destinos = [d for d, _ in estado.evo.enviados]
    assert "5511988884444" in destinos      # boas-vindas ao cliente
    assert "5537999990000" in destinos      # vistoria pra equipe


def test_evento_repetido_e_ignorado(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    corpo = {"evento": "cobranca_paga", "cobranca_id": "B1"}
    cliente.post("/webhook/powercrm", headers=CABECALHO, json=corpo)
    cliente.post("/webhook/powercrm", headers=CABECALHO, json=corpo)
    assert len(estado.evo.enviados) == 2  # só o primeiro teve efeito


def test_cobranca_desconhecida_nao_quebra(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json={"evento": "cobranca_paga", "cobranca_id": "ZZZ"})
    assert r.status_code == 200
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_pagamento.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'disparo.pagamento'` (via 404 na rota)

- [ ] **Step 3: Implementar**

```python
# src/disparo/pagamento.py
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status

from disparo import eventos, handoff
from disparo.maquina import Status, status_de, transicionar

BOAS_VINDAS = ("Pagamento confirmado, {nome}! Seja bem-vindo à Porto Sul. "
               "A equipe já vai te chamar pra agendar a vistoria do veículo.")


def criar_rotas(estado) -> APIRouter:
    rotas = APIRouter()

    @rotas.post("/webhook/powercrm")
    async def receber(request: Request) -> dict:
        esperado = f"Bearer {estado.cfg.powercrm_webhook_token}"
        recebido = request.headers.get("Authorization", "")
        if not (estado.cfg.powercrm_webhook_token
                and secrets.compare_digest(recebido, esperado)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token invalido")

        corpo = await request.json()
        if corpo.get("evento") != "cobranca_paga":
            return {"ok": True}

        lead = estado.conn.execute(
            "SELECT * FROM leads WHERE cobranca_id = ?",
            (str(corpo.get("cobranca_id", "")),),
        ).fetchone()
        if lead is None:
            return {"ok": True}

        agora = datetime.now()
        if status_de(estado.conn, lead["id"]) != Status.AGUARDANDO_PAGAMENTO:
            return {"ok": True}  # repetido ou fora de ordem

        transicionar(estado.conn, lead["id"], Status.PAGO, agora)
        primeiro_nome = lead["nome"].split()[0]
        estado.evo.enviar_texto(lead["telefone_e164"],
                                BOAS_VINDAS.format(nome=primeiro_nome))
        handoff.avisar_vistoria(estado.conn, estado.evo,
                                estado.cfg.equipe_telefone, lead, agora)
        return {"ok": True}

    return rotas
```

Em `src/disparo/handoff.py`:

```python
def avisar_vistoria(conn: sqlite3.Connection, evo, telefone_equipe: str,
                    lead: sqlite3.Row, agora: datetime) -> None:
    fone = lead["telefone_e164"]
    texto = "\n".join([
        f"VENDA PAGA — {lead['nome']}",
        f"{lead['veiculo']} — placa {lead['placa'] or '?'}",
        f"Plano {lead['plano'] or '?'} · R$ {lead['mensalidade'] or '?'}/mês",
        "Agendar vistoria.",
        f"Conversa: wa.me/{fone}",
    ])
    evo.enviar_texto(telefone_equipe, texto)
    eventos.registrar(conn, "quente",
                      f"{lead['nome']} pagou — vistoria pendente",
                      agora, lead["id"])
```

Em `src/disparo/app.py`, dentro de `criar_app`:

```python
from disparo import pagamento, painel, webhook
...
    app.include_router(pagamento.criar_rotas(estado))
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_pagamento.py -v` — Expected: PASS (4 testes). Depois `pytest -q` inteira.

- [ ] **Step 5: Commit**

```bash
git add src/disparo/pagamento.py src/disparo/handoff.py src/disparo/app.py tests/test_pagamento.py
git commit -m "feat: webhook de pagamento com boas-vindas e aviso de vistoria"
```

---

### Task 8: Lembrete de 48h e escalada de boleto vencido

**Files:**
- Modify: `src/disparo/manutencao.py`, `src/disparo/app.py`
- Test: `tests/test_manutencao.py` (adicionar)

**Interfaces:**
- Consumes: `Status` (Task 2), `handoff.avisar_escalada` (Task 6), colunas `cobranca_enviada_em`/`lembrete_em`/`boleto_url` (Task 1).
- Produces:
  - `manutencao.cobrar_pendentes(conn, evo, telefone_equipe, agora) -> tuple[int, int]` — devolve `(lembretes_enviados, escalados)`. Regra: `AGUARDANDO_PAGAMENTO` com `cobranca_enviada_em` < agora−48h e `lembrete_em` NULL → envia lembrete único e grava `lembrete_em`; com `cobranca_enviada_em` < agora−72h → `ESCALADO` + `avisar_escalada` (motivo `"boleto ha 72h sem pagamento"`).
  - `LEMBRETE = "Oi {nome}, tudo bem? Só lembrando do boleto da proteção: {boleto}. Dá pra pagar pelo PIX no próprio boleto. Qualquer dúvida me chama."`
  - Job novo em `app.main()`: `cobrar_pendentes` a cada hora.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_manutencao.py`:

```python
def _com_boleto(conn, lead, enviado_em):
    from disparo.maquina import Status, transicionar
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.NEGOCIANDO, AGORA)
    transicionar(conn, lead, Status.AGUARDANDO_PAGAMENTO, AGORA)
    conn.execute(
        "UPDATE leads SET cobranca_id='B1', boleto_url='https://p/b1', "
        "cobranca_enviada_em=? WHERE id=?",
        (enviado_em.isoformat(), lead))
    conn.commit()


class EvoFalsa:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return "WA"


def test_lembrete_unico_apos_48h(conn, lead):
    from disparo.manutencao import cobrar_pendentes
    _com_boleto(conn, lead, AGORA - timedelta(hours=49))
    evo = EvoFalsa()
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (1, 0)
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (0, 0)
    assert len(evo.enviados) == 1
    assert "boleto" in evo.enviados[0][1].lower()


def test_antes_de_48h_nada(conn, lead):
    from disparo.manutencao import cobrar_pendentes
    _com_boleto(conn, lead, AGORA - timedelta(hours=47))
    assert cobrar_pendentes(conn, EvoFalsa(), "x", AGORA) == (0, 0)


def test_72h_escala(conn, lead):
    from disparo.manutencao import cobrar_pendentes
    from disparo.maquina import Status, status_de
    _com_boleto(conn, lead, AGORA - timedelta(hours=73))
    evo = EvoFalsa()
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (0, 1)
    assert status_de(conn, lead) == Status.ESCALADO
    assert any(d == "5537999990000" for d, _ in evo.enviados)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_manutencao.py -v`
Expected: FAIL com `ImportError: cannot import name 'cobrar_pendentes'`

- [ ] **Step 3: Implementar**

Em `src/disparo/manutencao.py`:

```python
from disparo import eventos, handoff
from disparo.maquina import Status, transicionar

LEMBRETE = ("Oi {nome}, tudo bem? Só lembrando do boleto da proteção: {boleto}. "
            "Dá pra pagar pelo PIX no próprio boleto. Qualquer dúvida me chama.")


def cobrar_pendentes(conn: sqlite3.Connection, evo, telefone_equipe: str,
                     agora: datetime) -> tuple[int, int]:
    corte_48 = (agora - timedelta(hours=48)).isoformat()
    corte_72 = (agora - timedelta(hours=72)).isoformat()

    vencidos = conn.execute(
        "SELECT * FROM leads WHERE status = 'aguardando_pagamento' "
        "AND cobranca_enviada_em < ?", (corte_72,),
    ).fetchall()
    for lead in vencidos:
        transicionar(conn, lead["id"], Status.ESCALADO, agora)
        handoff.avisar_escalada(conn, evo, telefone_equipe, lead,
                                "boleto ha 72h sem pagamento", agora)

    pendentes = conn.execute(
        "SELECT * FROM leads WHERE status = 'aguardando_pagamento' "
        "AND cobranca_enviada_em < ? AND lembrete_em IS NULL", (corte_48,),
    ).fetchall()
    for lead in pendentes:
        primeiro_nome = lead["nome"].split()[0]
        evo.enviar_texto(lead["telefone_e164"], LEMBRETE.format(
            nome=primeiro_nome, boleto=lead["boleto_url"]))
        conn.execute("UPDATE leads SET lembrete_em = ? WHERE id = ?",
                     (agora.isoformat(), lead["id"]))
        conn.commit()
        eventos.registrar(conn, "sistema",
                          f"Lembrete de boleto para {lead['nome']}",
                          agora, lead["id"])

    return len(pendentes), len(vencidos)
```

Em `src/disparo/app.py`, dentro de `main()`, após os jobs existentes:

```python
    agenda.add_job(
        lambda: cobrar_pendentes(estado.conn, estado.evo,
                                 estado.cfg.equipe_telefone, datetime.now()),
        "interval", hours=1, id="cobranca_pendente",
    )
```

Import: `from disparo.manutencao import backup, cobrar_pendentes, encerrar_sem_resposta`.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest -q` — Expected: suíte inteira verde.

- [ ] **Step 5: Commit**

```bash
git add src/disparo/manutencao.py src/disparo/app.py tests/test_manutencao.py
git commit -m "feat: lembrete de boleto em 48h e escalada em 72h"
```

---

### Task 9: Estado montado com Power CRM e painel com funil

**Files:**
- Modify: `src/disparo/app.py` (montar_estado + repassar powercrm ao webhook), `src/disparo/webhook.py`, `src/disparo/painel.py`, `src/disparo/static/painel.html`
- Test: `tests/test_painel.py` (adicionar), `tests/test_webhook.py` (adicionar)

**Interfaces:**
- Consumes: `PowerCRM` (Task 3), `processar(..., powercrm=)` (Task 6), `cfg` (Task 1).
- Produces:
  - `montar_estado()` cria `estado.powercrm = PowerCRM(cfg.powercrm_base_url, cfg.powercrm_token, httpx.Client(timeout=30)) if cfg.powercrm_base_url else None`.
  - `webhook.criar_rotas` repassa `estado.powercrm` (via `getattr(estado, "powercrm", None)`) para `processar`.
  - `/api/estado` ganha `"funil": {"negociando": int, "aguardando_pagamento": int, "pagos": int}` (contagem total por status).
  - `painel.html`: `<option>` novos no filtro (`negociando`, `aguardando_pagamento`, `pago`, `escalado`), pills correspondentes, e o funil renderizado na nota do card do disjuntor: `Funil: N negociando · N boletos · N pagos`.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_painel.py`:

```python
def test_estado_traz_funil(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    dados = cliente.get("/api/estado", auth=AUTH).json()
    assert dados["funil"] == {"negociando": 0, "aguardando_pagamento": 0,
                              "pagos": 0}
```

Adicionar ao fim de `tests/test_webhook.py`:

```python
def test_webhook_repassa_powercrm(conn, lead):
    conn.execute("UPDATE leads SET status = 'contatado' WHERE id = ?", (lead,))
    conn.commit()
    estado = _estado(conn)
    estado.powercrm = object()  # sentinela: só precisa chegar em processar
    capturado = {}
    import disparo.webhook as wh
    original = wh.processar

    def espiao(*args, **kwargs):
        capturado["powercrm"] = kwargs.get("powercrm") or args[-1]

    wh.processar = espiao
    try:
        cliente = TestClient(criar_app(estado))
        corpo = {"data": {"key": {"id": "WA-p", "remoteJid":
                                  "5511988884444@s.whatsapp.net",
                                  "fromMe": False},
                          "message": {"conversation": "oi"}}}
        cliente.post("/webhook", json=corpo)
    finally:
        wh.processar = original
    assert capturado["powercrm"] is estado.powercrm
```

E em `_estado` de `tests/test_webhook.py` e `tests/test_painel.py`, acrescentar `powercrm=None` no `SimpleNamespace`.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_painel.py tests/test_webhook.py -v`
Expected: FAIL (`KeyError: 'funil'` e sentinela não chega)

- [ ] **Step 3: Implementar**

Em `src/disparo/webhook.py`, na chamada de `add_task`:

```python
        tarefas.add_task(
            processar, estado.conn, estado.evo, estado.claude, estado.cfg,
            mensagem, datetime.now(), estado.rng, estado.dormir,
            getattr(estado, "powercrm", None),
        )
```

Em `src/disparo/painel.py`, no `ler_estado`, acrescentar antes do `return`:

```python
        contagens = dict(estado.conn.execute(
            "SELECT status, COUNT(*) FROM leads WHERE status IN "
            "('negociando', 'aguardando_pagamento', 'pago') GROUP BY status"
        ).fetchall())
```

e no dicionário devolvido:

```python
            "funil": {
                "negociando": contagens.get("negociando", 0),
                "aguardando_pagamento": contagens.get("aguardando_pagamento", 0),
                "pagos": contagens.get("pago", 0),
            },
```

Em `src/disparo/app.py`, `montar_estado` ganha (após criar `cfg`):

```python
        powercrm=(PowerCRM(cfg.powercrm_base_url, cfg.powercrm_token,
                           httpx.Client(timeout=30))
                  if cfg.powercrm_base_url else None),
```

Import: `from disparo.powercrm import PowerCRM`.

Em `src/disparo/static/painel.html`:

1. No `<select id="filtro">`, após a option `quente`... acrescentar:

```html
          <option value="negociando">Negociando</option>
          <option value="aguardando_pagamento">Aguardando pagamento</option>
          <option value="pago">Pago</option>
          <option value="escalado">Escalado</option>
```

2. No CSS das pills, acrescentar:

```css
  .pill[data-s="negociando"] { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); }
  .pill[data-s="aguardando_pagamento"] { background: var(--warn-soft); color: var(--warn); border-color: var(--warn); }
  .pill[data-s="pago"] { background: var(--ok-soft); color: var(--ok); border-color: var(--ok); }
  .pill[data-s="escalado"] { background: var(--crit-soft); color: var(--crit); border-color: var(--crit); }
```

3. No card do disjuntor, acrescentar `<span class="gauge-note" id="funil"></span>` e, em `carregarEstado()`:

```js
    if (e.funil) {
      document.getElementById("funil").textContent =
        `Funil: ${e.funil.negociando} negociando · ` +
        `${e.funil.aguardando_pagamento} boletos · ${e.funil.pagos} pagos`;
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest -q` — Expected: suíte inteira verde.

- [ ] **Step 5: Commit**

```bash
git add src/disparo/app.py src/disparo/webhook.py src/disparo/painel.py src/disparo/static/painel.html tests/test_painel.py tests/test_webhook.py
git commit -m "feat: power crm no estado da aplicacao e funil no painel"
```

---

### Task 10: Documentação, .env e verificação final

**Files:**
- Modify: `.env.example`, `README.md`

**Interfaces:**
- Consumes: tudo acima.
- Produces: serviço documentado e pronto para o deploy da Etapa 2.

- [ ] **Step 1: Atualizar `.env.example`**

Acrescentar ao fim:

```
POWERCRM_BASE_URL=
POWERCRM_TOKEN=
POWERCRM_WEBHOOK_TOKEN=
EQUIPE_TELEFONE=
MODELO_TRIAGEM=claude-haiku-4-5
MODELO_FECHAMENTO=claude-sonnet-5
```

- [ ] **Step 2: Atualizar o `README.md`**

Acrescentar após a seção "Primeira operação":

```markdown
## Venda autônoma (Etapa 2)

Com `POWERCRM_BASE_URL` e `POWERCRM_TOKEN` no `.env`, a IA cota pela placa,
apresenta o preço de tabela, gera o boleto (pagável por PIX) e, pago, avisa a
equipe para agendar a vistoria. Sem essas variáveis o serviço opera no modo
da Etapa 1 (qualifica e escala para humano).

Configurar no Power CRM (Minha Empresa → Integrações):
1. Power API: gerar o token e colocar em `POWERCRM_TOKEN`.
2. Power Webhook: URL `https://seu-dominio/webhook/powercrm`, token igual ao
   `POWERCRM_WEBHOOK_TOKEN`, evento de pagamento confirmado.

Regras fixas: preço de tabela sem desconto; cobrança só após aceite explícito;
lembrete único de boleto em 48h; sem pagamento em 72h a equipe assume.
```

- [ ] **Step 3: Suíte final**

Run: `pytest -q`
Expected: tudo verde (Etapa 1 + Etapa 2).

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md
git commit -m "docs: operacao da venda autonoma no README e .env"
```

---

## Cobertura da especificação

| Requisito do spec | Tarefa |
|---|---|
| Config nova (Power CRM, equipe, modelos) | 1 |
| Colunas de cotação/cobrança no banco | 1 |
| Estados negociando/aguardando_pagamento/pago/escalado | 2 |
| Cliente Power API com hierarquia de erros | 3 |
| Ferramentas cotar/gerar_cobranca/escalar_humano | 4 |
| Laço de tool use no conversador | 5 |
| Roteiro de fechamento sem desconto, aceite explícito | 5 |
| Teto de turnos 20 → escalar | 5, 6 |
| Haiku na triagem, Sonnet no fechamento | 6 |
| Kill switch cobre a conversa | 6 |
| 2 falhas do Power CRM → escalar | 4, 6 |
| Aviso de escalada à equipe | 6 |
| Webhook de pagamento idempotente com token | 7 |
| Boas-vindas ao cliente + aviso de vistoria | 7 |
| Lembrete 48h / escalada 72h | 8 |
| Painel: filtros e funil | 9 |
| Modo degradado sem Power CRM (Etapa 1) | 6, 9 |
| README e .env | 10 |

## Pendências externas (não bloqueiam o código, bloqueiam a ativação)

1. Token da Power API → `POWERCRM_TOKEN`.
2. Doc real dos endpoints → conferir/ajustar `powercrm.py`, payload do webhook em `pagamento.py` e fakes.
3. Instância da Evolution do chip da Porto Sul (Etapa 1 também espera).
