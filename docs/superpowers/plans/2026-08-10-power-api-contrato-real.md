# Power API contrato real + cobrança pela vendedora — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adaptar o cliente Power CRM ao contrato real da Power API e substituir a geração automática de boleto por escalada à vendedora (decisão de 2026-08-10: a Power API não tem endpoint de cobrança).

**Architecture:** O cliente `PowerCRM.cotar` passa a fazer duas chamadas reais (`POST /api/quotation/add` + `GET /api/quotation/plansQuotation`). A ferramenta `gerar_cobranca` vira `fechar_venda`: registra o aceite, transiciona para `aguardando_pagamento` e avisa a equipe pra gerar o boleto manualmente no Power CRM. O webhook `/webhook/powercrm` passa a entender o envelope Webhook V2 (`payment.slip.paid` etc.), casando o lead por `data.quotationCode`.

**Tech Stack:** Python 3.12, FastAPI, httpx, respx, pytest, sqlite3.

## Global Constraints

- Rodar testes com `python -m pytest` a partir da raiz do repo (`C:\Users\yurid\disparo-veicular`).
- Nomes de código em português, como o restante do repo (`cotacao_id`, `avisar_fechamento`).
- Campos da Power API têm typos REAIS que devem ser preservados: `sucess` (resposta do add) e `negotationCode` (dentro de `quotationResponse`). NÃO corrigir a grafia ao ler o JSON.
- O Power CRM DESATIVA o webhook se receber qualquer resposta de erro — o endpoint `/webhook/powercrm` só pode devolver != 200 em token inválido.
- Commits pequenos por tarefa, mensagens em português no padrão do repo (`feat:`, `fix:`, `docs:`).
- Doc de referência da API: https://power-crm.readme.io/reference (índice em /llms.txt). Webhook V2: https://site.powercrm.com.br/como-funciona-as-webhooks-v2-do-powercrm/

## Contrato real (referência para todas as tarefas)

`POST {base}/api/quotation/add` — header `Authorization: Bearer {token}`, corpo `{"name": str, "phone": str, "plts": str}`. Resposta 201:

```json
{"sucess": true,
 "quotationResponse": {"quotationCode": "QTN-8F3A2C", "negotationCode": "NEG-4FA21B", "plan": 1, "protectedValue": 45000, "...": "..."},
 "errorVO": null}
```

Status 412 devolve `{"errors": ["..."]}`.

`GET {base}/api/quotation/plansQuotation?quotationCode=QTN-8F3A2C` — resposta 200:

```json
{"quotationCode": "QTN-8F3A2C", "acquisitionPrice": 250.0, "monthlyPrice": 189.9,
 "trackerPrice": 0, "plans": [{"planId": 7, "name": "Master", "isSelected": true, "active": true, "price": 189.9}]}
```

Webhook V2 (POST no nosso endpoint) — headers `Content-Type: application/json`, `User-Agent: PowerCRM-Webhooks/2.0`, `Authorization: Bearer {token do cadastro}`. Envelope:

```json
{"id": "evt_013", "type": "payment.slip.paid", "version": "2026-04-01",
 "occurredAt": "2026-07-20T13:41:00Z", "companyId": 123,
 "subject": {"type": "quotation", "id": "98765", "code": "QTN-8F3A2C"},
 "data": {"legacyType": "PAYMENT", "legacyStatus": "PAID_SLIP", "status": "Boleto pago",
          "negotiationCode": "NEG-4FA21B", "quotationCode": "QTN-8F3A2C", "hash": "hsh_9F7A11BC"},
 "metadata": {"correlationId": "corr_013", "source": "powercrm-backend.webhook.legacy-compat"}}
```

Tipos de pagamento confirmado: `payment.slip.paid`, `payment.pix.paid`, `payment.card.paid`, `payment.cash.paid` (dinheiro; nome inferido do padrão — o matcher também aceita `payment.*.paid` por prefixo/sufixo). Boleto gerado: `payment.slip.generated`. Boleto vencido: `payment.slip.overdue`.

---

### Task 1: Cliente PowerCRM com contrato real

**Files:**
- Modify: `src/disparo/powercrm.py` (reescrever)
- Test: `tests/test_powercrm.py` (reescrever)

**Interfaces:**
- Produces: `Cotacao(cotacao_id: str, negociacao_id: str, plano: str, mensalidade: str, adesao: str)` — mensalidade/adesão formatadas com vírgula ("189,90"). `PowerCRM.cotar(nome, telefone, placa) -> Cotacao`. Exceções `PowerCRMIndisponivel` e `PowerCRMRecusa(status, detalhe)` inalteradas. O método `gerar_cobranca` e a dataclass `Cobranca` DEIXAM DE EXISTIR.

- [ ] **Step 1: Reescrever os testes**

Substituir o conteúdo de `tests/test_powercrm.py` por:

```python
import httpx
import pytest
import respx

from disparo.powercrm import (Cotacao, PowerCRM, PowerCRMIndisponivel,
                              PowerCRMRecusa)

BASE = "https://api.powercrm.test"

ADD_OK = {
    "sucess": True,
    "quotationResponse": {"quotationCode": "QTN-1", "negotationCode": "NEG-1"},
    "errorVO": None,
}
PLANOS_OK = {
    "quotationCode": "QTN-1", "acquisitionPrice": 250.0, "monthlyPrice": 189.9,
    "plans": [
        {"planId": 5, "name": "Basico", "isSelected": False, "active": True},
        {"planId": 7, "name": "Master", "isSelected": True, "active": True},
    ],
}


@pytest.fixture
def cliente():
    with httpx.Client() as http:
        yield PowerCRM(BASE, "tok", http)


@respx.mock
def test_cotar_faz_as_duas_chamadas(cliente):
    rota_add = respx.post(f"{BASE}/api/quotation/add").respond(201, json=ADD_OK)
    rota_planos = respx.get(f"{BASE}/api/quotation/plansQuotation").respond(
        200, json=PLANOS_OK)
    c = cliente.cotar("Joao", "5537988884444", "ABC1D23")
    assert c == Cotacao("QTN-1", "NEG-1", "Master", "189,90", "250,00")
    corpo = rota_add.calls.last.request
    assert corpo.headers["Authorization"] == "Bearer tok"
    import json
    assert json.loads(corpo.content) == {
        "name": "Joao", "phone": "5537988884444", "plts": "ABC1D23"}
    assert rota_planos.calls.last.request.url.params["quotationCode"] == "QTN-1"


@respx.mock
def test_cotar_sem_plano_selecionado_usa_o_primeiro_ativo(cliente):
    planos = dict(PLANOS_OK)
    planos["plans"] = [
        {"planId": 1, "name": "Inativo", "isSelected": False, "active": False},
        {"planId": 5, "name": "Basico", "isSelected": False, "active": True},
    ]
    respx.post(f"{BASE}/api/quotation/add").respond(201, json=ADD_OK)
    respx.get(f"{BASE}/api/quotation/plansQuotation").respond(200, json=planos)
    assert cliente.cotar("Joao", "5537988884444", "ABC1D23").plano == "Basico"


@respx.mock
def test_add_sem_sucesso_vira_recusa(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(
        201, json={"sucess": False, "quotationResponse": None,
                   "errorVO": {"msg": "sem tabela de preco"}})
    with pytest.raises(PowerCRMRecusa):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_412_vira_recusa(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(
        412, json={"errors": ["placa invalida"]})
    with pytest.raises(PowerCRMRecusa) as e:
        cliente.cotar("Joao", "5537988884444", "XXX")
    assert e.value.status == 412


@respx.mock
def test_5xx_vira_indisponivel(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(503)
    with pytest.raises(PowerCRMIndisponivel):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_5xx_na_busca_de_planos_vira_indisponivel(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(201, json=ADD_OK)
    respx.get(f"{BASE}/api/quotation/plansQuotation").respond(500)
    with pytest.raises(PowerCRMIndisponivel):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_timeout_vira_indisponivel(cliente):
    respx.post(f"{BASE}/api/quotation/add").mock(
        side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(PowerCRMIndisponivel):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_powercrm.py -v`
Expected: FAIL — `ImportError` (Cotacao mudou de campos) ou asserts.

- [ ] **Step 3: Reescrever o cliente**

Substituir o conteúdo de `src/disparo/powercrm.py` por:

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
    negociacao_id: str
    plano: str
    mensalidade: str
    adesao: str


def _dinheiro(valor) -> str:
    return f"{float(valor):.2f}".replace(".", ",")


def _escolher_plano(planos: list[dict]) -> str:
    for plano in planos:
        if plano.get("isSelected"):
            return plano.get("name", "")
    for plano in planos:
        if plano.get("active"):
            return plano.get("name", "")
    return planos[0].get("name", "") if planos else ""


class PowerCRM:
    """Cliente da Power API (doc: https://power-crm.readme.io/reference)."""

    def __init__(self, base_url: str, token: str, http: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._cabecalhos = {"Authorization": f"Bearer {token}"}

    def _chamar(self, metodo: str, caminho: str, **kwargs) -> dict:
        try:
            resposta = self._http.request(
                metodo, f"{self._base}{caminho}",
                headers=self._cabecalhos, **kwargs,
            )
        except httpx.HTTPError as erro:
            raise PowerCRMIndisponivel(str(erro)) from erro
        if resposta.status_code >= 500:
            raise PowerCRMIndisponivel(f"HTTP {resposta.status_code}")
        if resposta.status_code >= 400:
            raise PowerCRMRecusa(resposta.status_code, resposta.text)
        return resposta.json()

    def cotar(self, nome: str, telefone: str, placa: str) -> Cotacao:
        dados = self._chamar("POST", "/api/quotation/add", json={
            "name": nome, "phone": telefone, "plts": placa,
        })
        # "sucess" e "negotationCode" são typos da própria API — não corrigir
        cotacao = dados.get("quotationResponse") or {}
        codigo = cotacao.get("quotationCode")
        if not dados.get("sucess") or not codigo:
            raise PowerCRMRecusa(200, f"cotacao recusada: {dados.get('errorVO')}")
        planos = self._chamar("GET", "/api/quotation/plansQuotation",
                              params={"quotationCode": codigo})
        return Cotacao(
            cotacao_id=str(codigo),
            negociacao_id=str(cotacao.get("negotationCode") or ""),
            plano=_escolher_plano(planos.get("plans") or []),
            mensalidade=_dinheiro(planos["monthlyPrice"]),
            adesao=_dinheiro(planos["acquisitionPrice"]),
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_powercrm.py -v`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add src/disparo/powercrm.py tests/test_powercrm.py
git commit -m "feat: cliente power api com contrato real de cotacao"
```

---

### Task 2: Coluna negociacao_id e cotar gravando o código da negociação

**Files:**
- Modify: `src/disparo/db.py:76-79`
- Modify: `src/disparo/ferramentas.py` (método `_cotar`)
- Test: `tests/test_ferramentas.py`

**Interfaces:**
- Consumes: `Cotacao` da Task 1 (campos `cotacao_id, negociacao_id, plano, mensalidade, adesao`).
- Produces: coluna `leads.negociacao_id` (TEXT, criada por `garantir_colunas`); `_cotar` grava `negociacao_id` junto com os demais campos.

- [ ] **Step 1: Atualizar o PowerFalso e o teste de cotação**

Em `tests/test_ferramentas.py`: remover `Cobranca` do import (linha 6); DELETAR os testes `test_cobranca_exige_cotacao`, `test_cobranca_grava_e_aguarda` e `test_cobranca_dupla_nao_explode` (a Task 3 traz os substitutos de `fechar_venda`); manter `test_spec_tem_as_tres_ferramentas` como está por enquanto (a Task 3 o atualiza junto com o spec); e atualizar `PowerFalso` para o novo `Cotacao`, sem `gerar_cobranca`:

```python
class PowerFalso:
    def __init__(self, fora_do_ar=False, recusa=False):
        self.fora_do_ar = fora_do_ar
        self.recusa = recusa

    def cotar(self, nome, telefone, placa):
        if self.fora_do_ar:
            raise PowerCRMIndisponivel("503")
        if self.recusa:
            raise PowerCRMRecusa(422, "placa invalida")
        return Cotacao("QTN-1", "NEG-1", "Master", "189,90", "250,00")
```

E em `test_cotar_grava_e_negocia`, trocar os asserts:

```python
    saida = f.executar("cotar", {"placa": "ABC1D23"})
    assert "189,90" in saida
    linha = conn.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
    assert linha["cotacao_id"] == "QTN-1"
    assert linha["negociacao_id"] == "NEG-1"
    assert linha["placa"] == "ABC1D23"
    assert status_de(conn, lead) == Status.NEGOCIANDO
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_ferramentas.py -v`
Expected: FAIL — `no such column: negociacao_id`.

- [ ] **Step 3: Implementar**

Em `src/disparo/db.py`, incluir a coluna na tupla:

```python
_COLUNAS_ETAPA_2 = (
    "placa", "cotacao_id", "negociacao_id", "plano", "mensalidade", "adesao",
    "cobranca_id", "boleto_url", "cobranca_enviada_em", "lembrete_em",
)
```

Em `src/disparo/ferramentas.py`, no `_cotar`, trocar o UPDATE:

```python
        self._conn.execute(
            "UPDATE leads SET placa = ?, cotacao_id = ?, negociacao_id = ?, "
            "plano = ?, mensalidade = ?, adesao = ? WHERE id = ?",
            (placa, cot.cotacao_id, cot.negociacao_id, cot.plano,
             cot.mensalidade, cot.adesao, self._lead),
        )
```

- [ ] **Step 4: Rodar**

Run: `python -m pytest tests/test_ferramentas.py -v`
Expected: PASS (todos os que restaram).

- [ ] **Step 5: Commit**

```bash
git add src/disparo/db.py src/disparo/ferramentas.py tests/test_ferramentas.py
git commit -m "feat: cotar grava codigo da negociacao do power crm"
```

---

### Task 3: Ferramenta fechar_venda no lugar de gerar_cobranca

**Files:**
- Modify: `src/disparo/ferramentas.py`
- Modify: `src/disparo/handoff.py` (nova função `avisar_fechamento`)
- Modify: `src/disparo/resposta.py` (disparar o aviso quando `ferramentas.fechou`)
- Test: `tests/test_ferramentas.py`, `tests/test_resposta.py`

**Interfaces:**
- Consumes: `transicionar`/`Status` de `maquina.py`; padrão de flag `escalou` já existente em `Ferramentas`.
- Produces: tool `fechar_venda` (spec sem input); atributo `Ferramentas.fechou: bool`; `handoff.avisar_fechamento(conn, evo, telefone_equipe, lead: sqlite3.Row, agora: datetime) -> None`. `gerar_cobranca` deixa de existir (spec, método e roteamento).

- [ ] **Step 1: Testes da ferramenta**

Em `tests/test_ferramentas.py`, atualizar `test_spec_tem_as_tres_ferramentas` para esperar `{"cotar", "fechar_venda", "escalar_humano"}` e adicionar:

```python
def test_fechar_venda_exige_cotacao(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    assert f.executar("fechar_venda", {}).startswith("erro:")
    assert f.fechou is False


def test_fechar_venda_aguarda_pagamento_e_marca_flag(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    f.executar("cotar", {"placa": "ABC1D23"})
    saida = f.executar("fechar_venda", {})
    assert "equipe" in saida
    assert f.fechou is True
    linha = conn.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
    assert linha["cobranca_enviada_em"] == AGORA.isoformat()
    assert status_de(conn, lead) == Status.AGUARDANDO_PAGAMENTO


def test_fechar_venda_dupla_nao_explode(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    f.executar("cotar", {"placa": "ABC1D23"})
    f.executar("fechar_venda", {})
    saida = f.executar("fechar_venda", {})
    assert "ja registrada" in saida
    assert status_de(conn, lead) == Status.AGUARDANDO_PAGAMENTO
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_ferramentas.py -v`
Expected: FAIL — `erro: ferramenta desconhecida fechar_venda`.

- [ ] **Step 3: Implementar a ferramenta**

Em `src/disparo/ferramentas.py`:

1. No `FERRAMENTAS_SPEC`, substituir o bloco de `gerar_cobranca` por:

```python
    {
        "name": "fechar_venda",
        "description": "Registra o aceite da venda e aciona a equipe pra gerar "
                       "e enviar o boleto (pagável por PIX). Use SOMENTE depois "
                       "de o cliente aceitar explicitamente o valor.",
        "input_schema": {"type": "object", "properties": {}},
    },
```

2. No `__init__`, adicionar `self.fechou = False` ao lado de `self.escalou = False`.

3. No `executar`, trocar o roteamento de `gerar_cobranca` por:

```python
        if nome == "fechar_venda":
            return self._fechar_venda()
```

4. Substituir o método `_gerar_cobranca` inteiro por:

```python
    def _fechar_venda(self) -> str:
        lead = self._linha()
        if not lead["cotacao_id"]:
            return "erro: nenhuma cotacao feita"
        if status_de(self._conn, self._lead) == Status.AGUARDANDO_PAGAMENTO:
            return "venda ja registrada: a equipe esta cuidando do boleto"
        transicionar(self._conn, self._lead, Status.AGUARDANDO_PAGAMENTO,
                     self._agora)
        self._conn.execute(
            "UPDATE leads SET cobranca_enviada_em = ? WHERE id = ?",
            (self._agora.isoformat(), self._lead),
        )
        self._conn.commit()
        eventos.registrar(self._conn, "sistema",
                          "Venda fechada — boleto com a equipe",
                          self._agora, self._lead)
        self.fechou = True
        return ("venda registrada: a equipe vai gerar o boleto e enviar "
                "aqui na conversa em instantes")
```

- [ ] **Step 4: Rodar**

Run: `python -m pytest tests/test_ferramentas.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Teste do aviso à equipe (resposta.py)**

Em `tests/test_resposta.py` já existem fakes de cliente Claude que devolvem `tool_use` (ver `test_duas_falhas_do_powercrm_escalam`). Adicionar no mesmo estilo:

```python
def test_fechamento_avisa_equipe(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)

    from disparo.powercrm import Cotacao

    class PowerOk:
        def cotar(self, nome, telefone, placa):
            return Cotacao("QTN-1", "NEG-1", "Master", "189,90", "250,00")

    from types import SimpleNamespace as NS
    respostas = iter([
        NS(parsed_output=None, content=[
            NS(type="tool_use", id="t1", name="cotar",
               input={"placa": "ABC1D23"})]),
        NS(parsed_output=None, content=[
            NS(type="tool_use", id="t2", name="fechar_venda", input={})]),
        NS(parsed_output=_q(resposta="Fechado, o boleto chega em instantes."),
           content=[]),
    ])
    cliente = NS(messages=NS(parse=lambda **kw: next(respostas)))
    evo = EvoFalsa()
    processar(conn, evo, cliente, CFG, _msg("fecho sim"), AGORA, RNG,
              dormir=lambda s: None, powercrm=PowerOk())
    assert status_de(conn, lead) == Status.AGUARDANDO_PAGAMENTO
    texto_equipe = next(
        t for d, t in evo.enviados if d == CFG.equipe_telefone)
    assert "VENDA FECHADA" in texto_equipe
    assert "QTN-1" in texto_equipe
```

Além disso, nos fakes `PowerQuebrado` de `test_duas_falhas_do_powercrm_escalam` e `test_falhas_powercrm_nao_sobrescreve_opt_out`, apagar o método `gerar_cobranca` (código morto — a tool não existe mais).

- [ ] **Step 6: Rodar e ver falhar**

Run: `python -m pytest tests/test_resposta.py -v`
Expected: FAIL no teste novo.

- [ ] **Step 7: Implementar aviso**

Em `src/disparo/handoff.py`, adicionar:

```python
def avisar_fechamento(conn: sqlite3.Connection, evo, telefone_equipe: str,
                      lead: sqlite3.Row, agora: datetime) -> None:
    fone = lead["telefone_e164"]
    texto = "\n".join([
        f"VENDA FECHADA — {lead['nome']}",
        f"{lead['veiculo']} — placa {lead['placa'] or '?'}",
        f"Plano {lead['plano'] or '?'} · R$ {lead['mensalidade'] or '?'}/mês "
        f"· adesão R$ {lead['adesao'] or '?'}",
        f"Gerar o boleto no Power CRM (cotação {lead['cotacao_id']}) "
        "e enviar pro cliente.",
        f"Conversa: wa.me/{fone}",
    ])
    evo.enviar_texto(telefone_equipe, texto)
    eventos.registrar(conn, "quente",
                      f"{lead['nome']} fechou — boleto com a equipe",
                      agora, lead["id"])
```

Em `src/disparo/resposta.py`, logo APÓS o bloco `conn.execute("UPDATE leads SET turnos = ...)` / `conn.commit()` (linha ~124) e ANTES de `novo_status = _DECISAO_PARA_STATUS.get(decisao)`:

```python
    if ferramentas is not None and ferramentas.fechou:
        handoff.avisar_fechamento(
            conn, evo, cfg.equipe_telefone,
            _lead_por_telefone(conn, mensagem.telefone), agora,
        )
```

(Rebusca o lead porque a linha em memória é anterior ao `cotar`/`fechar_venda`.)

- [ ] **Step 8: Rodar**

Run: `python -m pytest tests/test_resposta.py tests/test_ferramentas.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/disparo/ferramentas.py src/disparo/handoff.py src/disparo/resposta.py tests/test_ferramentas.py tests/test_resposta.py
git commit -m "feat: fechar_venda escala boleto pra equipe no lugar de gerar cobranca"
```

---

### Task 4: Prompt do conversador — etapa 9 com fechar_venda

**Files:**
- Modify: `src/disparo/conversador.py:44-46`
- Test: `tests/test_conversador.py`

**Interfaces:**
- Consumes: `FERRAMENTAS_SPEC` (agora com `fechar_venda`).
- Produces: prompt sem menção a `gerar_cobranca`/link de boleto.

- [ ] **Step 1: Atualizar referências nos testes**

`grep -n "gerar_cobranca" tests/test_conversador.py` — trocar cada ocorrência por `fechar_venda` (nomes de tool_use em fakes e asserts). Se algum teste assertar texto de boleto/link na resposta, ajustar para o novo fluxo (equipe envia).

- [ ] **Step 2: Rodar e ver falhar (se houve ocorrência)**

Run: `python -m pytest tests/test_conversador.py -v`
Expected: FAIL nas ocorrências trocadas (ou PASS direto se o arquivo não referenciava a tool — seguir mesmo assim).

- [ ] **Step 3: Reescrever a Etapa 9 do prompt**

Em `src/disparo/conversador.py`, substituir as linhas da Etapa 9:

```
Etapa 9 — aceite: só use fechar_venda depois de um sim explícito ("fecho",
  "pode mandar", "aceito"). Depois avise que a equipe vai mandar o boleto
  aqui na conversa em instantes e que dá pra pagar pelo PIX no próprio
  boleto. Encerre educadamente e aguarde.
```

- [ ] **Step 4: Rodar**

Run: `python -m pytest tests/test_conversador.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/disparo/conversador.py tests/test_conversador.py
git commit -m "feat: prompt de aceite usa fechar_venda"
```

---

### Task 5: Webhook de pagamento no formato Webhook V2

**Files:**
- Modify: `src/disparo/pagamento.py` (reescrever o handler)
- Test: `tests/test_pagamento.py` (reescrever payloads)

**Interfaces:**
- Consumes: coluna `leads.cotacao_id`; `Status`/`transicionar`; `handoff.avisar_vistoria`; `BOAS_VINDAS` inalterado.
- Produces: `/webhook/powercrm` entendendo o envelope V2. Match do lead por `data.quotationCode` == `leads.cotacao_id`. Regras: 401 só em token errado; TODO o resto devolve 200 `{"ok": true}` (Power desativa webhook em erro).

- [ ] **Step 1: Reescrever os testes**

Substituir em `tests/test_pagamento.py` os corpos de requisição e adicionar casos novos. Manter `EvoFalsa`, `_estado` e `_aguardando` (trocar em `_aguardando` a linha do UPDATE por `"UPDATE leads SET cotacao_id = 'QTN-1' WHERE id = ?"`). Payload helper e testes:

```python
def _evento(tipo, cotacao="QTN-1"):
    return {
        "id": "evt_1", "type": tipo, "version": "2026-04-01",
        "occurredAt": "2026-08-07T15:00:00Z", "companyId": 1,
        "subject": {"type": "quotation", "id": "98765", "code": cotacao},
        "data": {"legacyType": "PAYMENT", "status": "x",
                 "negotiationCode": "NEG-1", "quotationCode": cotacao,
                 "hash": ""},
        "metadata": {"correlationId": "c1", "source": "powercrm"},
    }


def test_sem_token_e_401(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm", json=_evento("payment.slip.paid"))
    assert r.status_code == 401


def test_boleto_pago_confirma_avisa_cliente_e_equipe(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.paid"))
    assert r.status_code == 200
    assert status_de(conn, lead) == Status.PAGO
    destinos = [d for d, _ in estado.evo.enviados]
    assert "5511988884444" in destinos      # boas-vindas ao cliente
    assert "5537999990000" in destinos      # vistoria pra equipe


def test_pix_pago_tambem_confirma(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    cliente.post("/webhook/powercrm", headers=CABECALHO,
                 json=_evento("payment.pix.paid"))
    assert status_de(conn, lead) == Status.PAGO


def test_evento_repetido_e_ignorado(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    cliente.post("/webhook/powercrm", headers=CABECALHO,
                 json=_evento("payment.slip.paid"))
    cliente.post("/webhook/powercrm", headers=CABECALHO,
                 json=_evento("payment.slip.paid"))
    assert len(estado.evo.enviados) == 2  # só o primeiro teve efeito


def test_boleto_gerado_rearma_o_lembrete(conn, lead):
    _aguardando(conn, lead)
    conn.execute("UPDATE leads SET cobranca_enviada_em = '2026-08-01T00:00:00', "
                 "lembrete_em = '2026-08-03T00:00:00' WHERE id = ?", (lead,))
    conn.commit()
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.generated"))
    assert r.status_code == 200
    linha = conn.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
    assert linha["cobranca_enviada_em"] != "2026-08-01T00:00:00"
    assert linha["lembrete_em"] is None
    assert status_de(conn, lead) == Status.AGUARDANDO_PAGAMENTO


def test_pagamento_apos_escalada_avisa_equipe_sem_transicionar(conn, lead):
    _aguardando(conn, lead)
    transicionar(conn, lead, Status.ESCALADO, AGORA)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.paid"))
    assert r.status_code == 200
    assert status_de(conn, lead) == Status.ESCALADO  # nenhuma transição
    destinos = [d for d, _ in estado.evo.enviados]
    assert "5537999990000" in destinos  # equipe alertada


def test_cotacao_desconhecida_nao_quebra(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.paid", cotacao="ZZZ"))
    assert r.status_code == 200


def test_evento_irrelevante_devolve_200(conn, lead):
    _aguardando(conn, lead)
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("negotiation.stage.changed"))
    assert r.status_code == 200


def test_corpo_invalido_devolve_200(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm",
                     headers={**CABECALHO, "Content-Type": "application/json"},
                     content=b"nao e json")
    assert r.status_code == 200
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_pagamento.py -v`
Expected: FAIL — handler antigo ignora `type`/`data`.

- [ ] **Step 3: Reescrever o handler**

Substituir o corpo de `criar_rotas` em `src/disparo/pagamento.py` por:

```python
_EVENTOS_PAGOS = {"payment.slip.paid", "payment.pix.paid",
                  "payment.card.paid", "payment.cash.paid"}


def _pago(tipo: str) -> bool:
    return tipo in _EVENTOS_PAGOS or (
        tipo.startswith("payment.") and tipo.endswith(".paid"))


def criar_rotas(estado) -> APIRouter:
    rotas = APIRouter()

    @rotas.post("/webhook/powercrm")
    async def receber(request: Request) -> dict:
        esperado = f"Bearer {estado.cfg.powercrm_webhook_token}"
        recebido = request.headers.get("Authorization", "")
        if not (estado.cfg.powercrm_webhook_token
                and secrets.compare_digest(recebido, esperado)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token invalido")

        # o Power desativa o webhook se receber erro: daqui pra baixo, sempre 200
        try:
            corpo = await request.json()
        except Exception:
            return {"ok": True}
        tipo = str(corpo.get("type", ""))
        dados = corpo.get("data") or {}
        codigo = str(dados.get("quotationCode") or "")
        if not codigo:
            return {"ok": True}
        lead = estado.conn.execute(
            "SELECT * FROM leads WHERE cotacao_id = ?", (codigo,)).fetchone()
        if lead is None:
            return {"ok": True}

        agora = datetime.now()
        status_atual = status_de(estado.conn, lead["id"])

        if tipo == "payment.slip.generated":
            if status_atual == Status.AGUARDANDO_PAGAMENTO:
                estado.conn.execute(
                    "UPDATE leads SET cobranca_enviada_em = ?, "
                    "lembrete_em = NULL WHERE id = ?",
                    (agora.isoformat(), lead["id"]),
                )
                estado.conn.commit()
                eventos.registrar(estado.conn, "sistema",
                                  "Boleto gerado no Power CRM", agora,
                                  lead["id"])
            return {"ok": True}

        if not _pago(tipo):
            return {"ok": True}

        if status_atual != Status.AGUARDANDO_PAGAMENTO:
            if status_atual != Status.PAGO:
                eventos.registrar(
                    estado.conn, "alerta",
                    f"pagamento recebido de {lead['nome']} apos escalada — "
                    f"conferir cotacao {codigo}",
                    agora, lead["id"],
                )
                estado.evo.enviar_texto(
                    estado.cfg.equipe_telefone,
                    f"Pagamento recebido de {lead['nome']} depois da escalada. "
                    f"Confere a cotação {codigo} no Power CRM.",
                )
            return {"ok": True}  # repetido, fora de ordem ou pós-escalada

        transicionar(estado.conn, lead["id"], Status.PAGO, agora)
        estado.evo.enviar_texto(
            lead["telefone_e164"],
            BOAS_VINDAS.format(nome=primeiro_nome(lead["nome"])))
        handoff.avisar_vistoria(estado.conn, estado.evo,
                                estado.cfg.equipe_telefone, lead, agora)
        return {"ok": True}

    return rotas
```

(Manter imports existentes; `eventos` já é importado no módulo.)

- [ ] **Step 4: Rodar**

Run: `python -m pytest tests/test_pagamento.py -v`
Expected: PASS (10 testes).

- [ ] **Step 5: Commit**

```bash
git add src/disparo/pagamento.py tests/test_pagamento.py
git commit -m "feat: webhook de pagamento no formato webhook v2 do power crm"
```

---

### Task 6: Lembrete de boleto sem link

**Files:**
- Modify: `src/disparo/manutencao.py:12-13,54-56`
- Test: `tests/test_manutencao.py`

**Interfaces:**
- Consumes: `leads.boleto_url` (pode ser NULL agora — a equipe manda o boleto direto na conversa).
- Produces: dois templates (`LEMBRETE_COM_LINK`, `LEMBRETE_SEM_LINK`); `cobrar_pendentes` escolhe pelo preenchimento de `boleto_url`.

- [ ] **Step 1: Teste**

Em `tests/test_manutencao.py`, o helper `_com_boleto` seta `boleto_url`. Adicionar um helper sem boleto e o teste:

```python
def _sem_boleto(conn, lead, enviado_em):
    from disparo.maquina import Status, transicionar
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.NEGOCIANDO, AGORA)
    transicionar(conn, lead, Status.AGUARDANDO_PAGAMENTO, AGORA)
    conn.execute(
        "UPDATE leads SET cotacao_id='QTN-1', cobranca_enviada_em=? WHERE id=?",
        (enviado_em.isoformat(), lead))
    conn.commit()


def test_lembrete_sem_boleto_url_nao_mostra_link(conn, lead):
    from datetime import timedelta
    from disparo.manutencao import cobrar_pendentes
    _sem_boleto(conn, lead, AGORA - timedelta(hours=49))
    evo = EvoFalsa()
    assert cobrar_pendentes(conn, evo, "5537999990000", AGORA) == (1, 0)
    (destino, texto) = evo.enviados[0]
    assert "None" not in texto
    assert "boleto" in texto.lower()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_manutencao.py -v`
Expected: FAIL — template atual interpola `boleto=None`.

- [ ] **Step 3: Implementar**

Em `src/disparo/manutencao.py`:

```python
LEMBRETE_COM_LINK = ("Oi {nome}, tudo bem? Só lembrando do boleto da proteção: "
                     "{boleto}. Dá pra pagar pelo PIX no próprio boleto. "
                     "Qualquer dúvida me chama.")
LEMBRETE_SEM_LINK = ("Oi {nome}, tudo bem? Só lembrando do boleto da proteção "
                     "que a equipe te mandou aqui. Dá pra pagar pelo PIX no "
                     "próprio boleto. Qualquer dúvida me chama.")
```

E no loop de `pendentes`:

```python
    for lead in pendentes:
        if lead["boleto_url"]:
            texto = LEMBRETE_COM_LINK.format(
                nome=primeiro_nome(lead["nome"]), boleto=lead["boleto_url"])
        else:
            texto = LEMBRETE_SEM_LINK.format(nome=primeiro_nome(lead["nome"]))
        evo.enviar_texto(lead["telefone_e164"], texto)
```

(Remover a constante antiga `LEMBRETE`; atualizar importações/usos se algum teste a referenciar.)

- [ ] **Step 4: Rodar**

Run: `python -m pytest <arquivo encontrado> -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/disparo/manutencao.py tests/
git commit -m "feat: lembrete de boleto funciona sem link"
```

---

### Task 7: Varredura final, docs e suíte completa

**Files:**
- Modify: `README.md` (seção de operação da venda autônoma)
- Modify: `.env.example` (comentário sobre onde nasce cada token)
- Verify: suíte completa

- [ ] **Step 1: Varredura**

`grep -rn "gerar_cobranca\|Cobranca\|cobranca_paga\|url_boleto\|pix_copia_cola" src/ tests/ README.md` — o grep deve devolver só `cobranca_id`/`cobranca_enviada_em` (colunas legadas) e usos legítimos. Qualquer resto de código morto: remover.

- [ ] **Step 2: README**

Atualizar a seção da venda autônoma: cotação usa `POST /api/quotation/add` + `GET /api/quotation/plansQuotation`; aceite aciona `fechar_venda` (equipe gera o boleto no Power CRM manualmente); webhook é o Webhook V2 (payload com `type`/`data.quotationCode`, cadastrar tipo "Status do pagamento" com token = `POWERCRM_WEBHOOK_TOKEN`); lembrar que o Power desativa o webhook se o endpoint responder erro.

- [ ] **Step 3: .env.example**

Anotar nos comentários: `POWERCRM_TOKEN` nasce em Minha Empresa → Plugins → Power API (Gerar Token, conceder acesso a dados de contato do lead); `POWERCRM_WEBHOOK_TOKEN` é um segredo NOSSO (gerar com `openssl rand -hex 24`) cadastrado no plugin Power Webhook.

- [ ] **Step 4: Suíte completa**

Run: `python -m pytest`
Expected: tudo verde (169+ testes).

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example
git commit -m "docs: operacao com contrato real da power api"
```

---

## Pós-execução (fora do repo — sessão com o usuário)

1. VPS `/opt/disparo/.env`: descomentar `POWERCRM_BASE_URL`/`POWERCRM_TOKEN` (já gravados), gerar e preencher `POWERCRM_WEBHOOK_TOKEN`, `git pull`, reinstalar/restart do serviço.
2. Nginx do host: criar rota pública `https://yurilinscrm.duckdns.org/webhook-powercrm` → `127.0.0.1:8010/webhook/powercrm` (NUNCA mexer na rota `/webhook-portosul` do CRM).
3. Power CRM: Plugins → Power Webhook → Adicionar (Título "Disparo pagamento", URL acima, Token = `POWERCRM_WEBHOOK_TOKEN`, Tipo "Status do pagamento", Status Ativo).
4. Teste de fumaça: cotação real com placa de teste; conferir card no funil do Power CRM.
