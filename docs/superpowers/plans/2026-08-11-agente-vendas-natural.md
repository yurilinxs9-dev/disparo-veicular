# Agente de Vendas Natural — Plano de Implementação (Seções 1 e 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conversa indistinguível de humano: debounce por lead com resposta única por bloco de mensagens, tempo percebido 15–60s, timestamps reais, prompt adaptativo com estilo natural e anti-repetição.

**Architecture:** Novo módulo `fila.py` (debounce + trava por lead, thread-safe — BackgroundTasks do FastAPI roda em threadpool). `resposta.processar` vira orquestração: registra entrada → debounce → trava → `_responder` (com regeneração se chegar mensagem durante a geração). Prompt do `conversador.py` reescrito como guia adaptativo. Spec: `docs/superpowers/specs/2026-08-11-agente-vendas-natural-design.md`.

**Tech Stack:** Python 3.12, FastAPI, sqlite3, pytest. Sem dependência nova.

## Global Constraints

- Código, comentários, nomes e mensagens de commit em português (convenção do projeto).
- Sem dependência nova no `pyproject.toml`.
- Testes com as fixtures de `tests/conftest.py` (`conn`, `lead`) e fakes no estilo de `tests/test_resposta.py` (`EvoFalsa`, `_claude`, `dormir=lambda s: None`).
- Rodar testes: `python -m pytest tests/ -q` (na raiz do repo).
- Limite de 160 caracteres por mensagem enviada (`humano.quebrar`) permanece.
- Tempo percebido total (debounce + leitura + resposta + digitando) deve caber em ~15–61s.
- A Seção 3 do spec (auto-aprendizado) NÃO faz parte deste plano — plano próprio depois do teste ao vivo.

---

### Task 1: Módulo `fila.py` — debounce e trava por lead

**Files:**
- Create: `src/disparo/fila.py`
- Test: `tests/test_fila.py`

**Interfaces:**
- Produces: classe `FilaPorLead` com métodos:
  - `chegou(lead_id: int, imagem_b64: str | None = None, media_type: str | None = None) -> int` — registra chegada, devolve sequencial.
  - `aguardar(lead_id: int, meu_seq: int, janela: float, dormir: Callable[[float], None]) -> bool` — dorme `janela`; devolve `False` se chegou mensagem mais nova durante a espera (a rodada mais nova cuida — é assim que o relógio "reinicia").
  - `assumir(lead_id: int) -> bool` — tenta virar a rodada ativa; se já tem rodada em andamento, marca pendência e devolve `False`.
  - `liberar(lead_id: int) -> bool` — encerra a rodada ativa; devolve `True` se ficou pendência (reprocessar).
  - `mudou(lead_id: int, seq: int) -> bool` — chegou mensagem depois de `seq`?
  - `seq_atual(lead_id: int) -> int`
  - `tirar_midia(lead_id: int) -> tuple[str, str] | None` — devolve e limpa a última imagem pendente `(b64, media_type)`.

- [ ] **Step 1: Escrever os testes**

```python
# tests/test_fila.py
from disparo.fila import FilaPorLead


def test_aguardar_passa_quando_nao_chega_mensagem_nova():
    fila = FilaPorLead()
    seq = fila.chegou(1)
    assert fila.aguardar(1, seq, 10.0, dormir=lambda s: None) is True


def test_aguardar_desiste_quando_chega_mensagem_mais_nova():
    fila = FilaPorLead()
    seq1 = fila.chegou(1)
    dormidas = []

    def dormir(s):
        dormidas.append(s)
        fila.chegou(1)  # mensagem nova chega durante a janela

    assert fila.aguardar(1, seq1, 10.0, dormir) is False
    assert dormidas == [10.0]


def test_rodada_mais_nova_assume_depois_da_desistencia():
    fila = FilaPorLead()
    fila.chegou(1)
    seq2 = fila.chegou(1)
    assert fila.aguardar(1, seq2, 10.0, dormir=lambda s: None) is True
    assert fila.assumir(1) is True


def test_assumir_durante_rodada_ativa_marca_pendencia():
    fila = FilaPorLead()
    fila.chegou(1)
    assert fila.assumir(1) is True
    assert fila.assumir(1) is False       # segunda rodada não entra
    assert fila.liberar(1) is True        # e deixou pendência
    assert fila.liberar(1) is False       # pendência consumida


def test_leads_diferentes_nao_interferem():
    fila = FilaPorLead()
    fila.chegou(1)
    assert fila.assumir(1) is True
    assert fila.assumir(2) is True


def test_mudou_detecta_chegada_posterior():
    fila = FilaPorLead()
    seq = fila.chegou(1)
    assert fila.mudou(1, seq) is False
    fila.chegou(1)
    assert fila.mudou(1, seq) is True
    assert fila.seq_atual(1) == seq + 1


def test_tirar_midia_devolve_ultima_imagem_e_limpa():
    fila = FilaPorLead()
    fila.chegou(1, imagem_b64="abc", media_type="image/jpeg")
    fila.chegou(1, imagem_b64="def", media_type="image/png")
    assert fila.tirar_midia(1) == ("def", "image/png")
    assert fila.tirar_midia(1) is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_fila.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'disparo.fila'`

- [ ] **Step 3: Implementar**

```python
# src/disparo/fila.py
from __future__ import annotations

import threading
from typing import Callable


class FilaPorLead:
    """Debounce e trava de processamento por lead. Thread-safe.

    O debounce não usa relógio: cada chegada incrementa um sequencial, e a
    rodada que acorda do sono só segue se ainda for a chegada mais recente.
    Assim, mensagem nova durante a janela "reinicia o relógio" naturalmente —
    a rodada dela dorme a janela inteira a partir da chegada dela.
    """

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._seq: dict[int, int] = {}
        self._ocupado: set[int] = set()
        self._pendente: set[int] = set()
        self._midia: dict[int, tuple[str, str]] = {}

    def chegou(self, lead_id: int, imagem_b64: str | None = None,
               media_type: str | None = None) -> int:
        with self._trava:
            self._seq[lead_id] = self._seq.get(lead_id, 0) + 1
            if imagem_b64:
                self._midia[lead_id] = (imagem_b64, media_type or "image/jpeg")
            return self._seq[lead_id]

    def aguardar(self, lead_id: int, meu_seq: int, janela: float,
                 dormir: Callable[[float], None]) -> bool:
        dormir(janela)
        with self._trava:
            return self._seq.get(lead_id) == meu_seq

    def assumir(self, lead_id: int) -> bool:
        with self._trava:
            if lead_id in self._ocupado:
                self._pendente.add(lead_id)
                return False
            self._ocupado.add(lead_id)
            return True

    def liberar(self, lead_id: int) -> bool:
        with self._trava:
            self._ocupado.discard(lead_id)
            if lead_id in self._pendente:
                self._pendente.discard(lead_id)
                return True
            return False

    def mudou(self, lead_id: int, seq: int) -> bool:
        with self._trava:
            return self._seq.get(lead_id, 0) != seq

    def seq_atual(self, lead_id: int) -> int:
        with self._trava:
            return self._seq.get(lead_id, 0)

    def tirar_midia(self, lead_id: int) -> tuple[str, str] | None:
        with self._trava:
            return self._midia.pop(lead_id, None)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_fila.py -q`
Expected: PASS (7 testes)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/fila.py tests/test_fila.py
git commit -m "feat: fila de debounce e trava por lead"
```

---

### Task 2: Recalibrar atrasos em `humano.py`

**Files:**
- Modify: `src/disparo/humano.py:16-28`
- Test: `tests/test_humano.py`

**Interfaces:**
- Produces: `janela_debounce(rng) -> float` (8–20s). `atraso_leitura` passa a 2–8s; `atraso_resposta` passa a 3–25s. `duracao_digitando` e `intervalo_entre_disparos` inalterados.
- Consumes: nada de tasks anteriores.

Total percebido: 8–20 (debounce) + 2–8 (leitura) + 3–25 (resposta) + 2–8 (digitando) = 15–61s.

- [ ] **Step 1: Atualizar/escrever testes**

Abrir `tests/test_humano.py`, ajustar os asserts de faixa existentes de `atraso_leitura` (era 3–20) e `atraso_resposta` (era 15–180) para as faixas novas, e acrescentar:

```python
def test_janela_debounce_entre_8_e_20_segundos():
    rng = random.Random(1)
    for _ in range(200):
        assert 8 <= humano.janela_debounce(rng) <= 20


def test_atraso_leitura_entre_2_e_8_segundos():
    rng = random.Random(1)
    for _ in range(200):
        assert 2 <= humano.atraso_leitura(rng) <= 8


def test_atraso_resposta_entre_3_e_25_segundos():
    rng = random.Random(1)
    for _ in range(200):
        assert 3 <= humano.atraso_resposta(rng) <= 25
```

(Adaptar imports ao estilo do arquivo existente; substituir testes de faixa antigos em vez de duplicar.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_humano.py -q`
Expected: FAIL — `janela_debounce` não existe; faixas antigas divergem

- [ ] **Step 3: Implementar**

Em `src/disparo/humano.py`, substituir as três funções de atraso:

```python
def atraso_leitura(rng: random.Random) -> float:
    """Segundos entre a mensagem chegar e ser marcada como lida."""
    return rng.uniform(2, 8)


def atraso_resposta(rng: random.Random) -> float:
    """Segundos entre ler a mensagem do lead e começar a responder."""
    return rng.uniform(3, 25)


def janela_debounce(rng: random.Random) -> float:
    """Segundos sem mensagem nova antes de responder um bloco."""
    return rng.uniform(8, 20)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_humano.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/disparo/humano.py tests/test_humano.py
git commit -m "feat: janela de debounce e atrasos recalibrados para 15-60s"
```

---

### Task 3: Refatorar `resposta.py` — bloco, regeneração e timestamp real

**Files:**
- Modify: `src/disparo/resposta.py`
- Test: `tests/test_resposta.py`

**Interfaces:**
- Consumes: `FilaPorLead` da Task 1 (`chegou/aguardar/assumir/liberar/mudou/seq_atual/tirar_midia`); `humano.janela_debounce` da Task 2.
- Produces: `processar(conn, evo, cliente_claude, cfg, mensagem, agora, rng, dormir=_time.sleep, powercrm=None, fila=None, agora_envio=datetime.now)` — assinatura compatível com a atual (novos parâmetros só-keyword no fim). `fila=None` cria uma `FilaPorLead` própria (testes unitários); em produção o app injeta a compartilhada.

Comportamento novo:
1. Entrada gravada antes do debounce (dedup por `wa_message_id` mantido).
2. Debounce: `fila.chegou` → `fila.aguardar(janela_debounce)`; se voltar `False`, a rodada morre (a mais nova cuida).
3. Trava: laço `assumir → _responder → liberar`; `liberar()==True` reprocessa.
4. `_responder` sai cedo se o histórico terminar em `saida` (nada novo a responder — evita resposta duplicada após regeneração).
5. Regeneração: antes de chamar `conversar`, guarda `seq_atual`; se `fila.mudou` depois da geração, re-lê o histórico e gera de novo (máximo 2 regenerações; na 3ª tentativa envia mesmo assim).
6. `marcar_lida` usa o `wa_message_id` da última entrada no banco (não o da mensagem que abriu a rodada).
7. Mensagens de saída gravadas com `agora_envio()` (hora real do envio), não com `agora`.
8. Imagem pendente vem de `fila.tirar_midia` e é anexada à última entrada do histórico.

- [ ] **Step 1: Escrever os testes novos**

Acrescentar em `tests/test_resposta.py` (aproveitando `EvoFalsa`, `_claude`, `_q`, `_msg`, `conn`, `lead`):

```python
from disparo.fila import FilaPorLead


class ClaudeContador:
    """Fake que conta chamadas e permite injetar efeito colateral por chamada."""

    def __init__(self, qualificacao, ao_chamar=None):
        self.chamadas = 0
        self._q = qualificacao
        self._ao_chamar = ao_chamar
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.chamadas += 1
        if self._ao_chamar:
            self._ao_chamar(self.chamadas)
        return _resposta_final(self._q)


def test_bloco_de_mensagens_rapidas_gera_uma_resposta_so(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q())
    # simula duas mensagens: a primeira desiste no debounce, a segunda responde
    processar(conn, evo, claude, CFG, _msg("Sim", "WA-b1"), AGORA, RNG,
              dormir=lambda s: fila.chegou(lead) if s >= 8 else None,
              fila=fila)
    # rodada 1 morreu no aguardar (chegou WA-b2 durante a janela); agora a rodada 2:
    processar(conn, evo, claude, CFG, _msg("Por que?", "WA-b2"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert claude.chamadas == 1          # uma geração para o bloco inteiro
    assert len({t for t, _ in evo.enviados}) == 1


def test_regenera_quando_chega_mensagem_durante_a_geracao(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()

    def chega_no_meio(chamada):
        if chamada == 1:  # simula mensagem nova enquanto o modelo gerava
            fila.chegou(lead)
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, "
                "wa_message_id, criado_em) VALUES (?, 'entrada', 'texto', "
                "'e outra coisa', 'WA-r2', ?)", (lead, AGORA.isoformat()))
            conn.commit()

    claude = ClaudeContador(_q(), ao_chamar=chega_no_meio)
    processar(conn, evo, claude, CFG, _msg("primeira", "WA-r1"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert claude.chamadas == 2          # 1ª descartada, regenerou


def test_regeneracao_para_na_terceira_tentativa(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q(), ao_chamar=lambda n: fila.chegou(lead))
    processar(conn, evo, claude, CFG, _msg("oi", "WA-t1"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert claude.chamadas == 3          # original + 2 regenerações, depois envia
    assert evo.enviados                  # enviou mesmo com fila mudando sempre


def test_saida_gravada_com_hora_real_do_envio(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    envio = datetime(2026, 8, 4, 11, 2, 30)
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None, agora_envio=lambda: envio)
    linha = conn.execute(
        "SELECT criado_em FROM mensagens WHERE direcao='saida'").fetchone()
    assert linha["criado_em"] == envio.isoformat()


def test_marca_lida_a_ultima_entrada_do_banco(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, wa_message_id, "
        "criado_em) VALUES (?, 'entrada', 'texto', 'antiga', 'WA-old', ?)",
        (lead, AGORA.isoformat()))
    conn.commit()
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg("nova", "WA-new"), AGORA, RNG,
              dormir=lambda s: None)
    assert evo.lidas == ["WA-new"]


def test_nao_responde_se_historico_termina_em_saida(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q())
    processar(conn, evo, claude, CFG, _msg("oi", "WA-g1"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    chamadas_antes = claude.chamadas
    # pendência artificial: rodada extra sem mensagem nova de entrada
    fila.chegou(lead)
    processar(conn, evo, claude, CFG,
              MensagemNormalizada("texto", "oi", "5511988884444", "WA-g1"),
              AGORA, RNG, dormir=lambda s: None, fila=fila)  # dup: não grava
    assert claude.chamadas == chamadas_antes  # nada novo, nenhuma geração
```

Nota: `test_bloco_de_mensagens_rapidas_gera_uma_resposta_so` usa o `dormir`
da primeira rodada para simular a chegada da segunda mensagem durante a
janela de debounce (o primeiro `dormir` com `s >= 8` é a janela; os atrasos
de leitura/resposta são menores só na comparação com 8 quando a janela
sorteada for ≥ 8 — a janela é sempre a primeira chamada de `dormir`, então
se preferir use um contador de chamadas em vez do valor de `s`).

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_resposta.py -q`
Expected: FAIL — `processar` não aceita `fila`/`agora_envio`

- [ ] **Step 3: Reescrever `resposta.py`**

Substituir `processar` por orquestração + `_responder`:

```python
# imports novos no topo:
from datetime import datetime
from disparo.fila import FilaPorLead

_MAX_REGENERACOES = 2


def _registrar_entrada(conn, lead_id: int, mensagem, agora) -> bool:
    """Grava a mensagem recebida. Devolve False se era duplicada."""
    cursor = conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, transcricao, "
        "wa_message_id, criado_em) VALUES (?, 'entrada', ?, ?, ?, ?, ?) "
        "ON CONFLICT(wa_message_id) DO NOTHING",
        (lead_id, mensagem.tipo, mensagem.texto,
         mensagem.texto if mensagem.tipo == "audio" else None,
         mensagem.wa_message_id, agora.isoformat()),
    )
    conn.commit()
    return cursor.rowcount > 0


def processar(conn, evo, cliente_claude, cfg, mensagem, agora, rng,
              dormir=_time.sleep, powercrm=None, *, fila=None,
              agora_envio=datetime.now) -> None:
    """Trata uma mensagem recebida: grava, espera a janela e responde o bloco."""
    fila = fila if fila is not None else FilaPorLead()
    lead = _lead_por_telefone(conn, mensagem.telefone)
    if lead is None:
        return
    if not robo_pode_falar(status_de(conn, lead["id"])):
        return
    gravou = _registrar_entrada(conn, lead["id"], mensagem, agora)
    if not gravou and not fila.mudou(lead["id"], fila.seq_atual(lead["id"])):
        # duplicada de webhook repetido: só segue se houver pendência real
        if not fila.assumir(lead["id"]):
            return
        try:
            _responder(conn, evo, cliente_claude, cfg, lead, agora, rng,
                       dormir, powercrm, fila, agora_envio)
        finally:
            fila.liberar(lead["id"])
        return

    if mensagem.transcricao_falhou:
        eventos.registrar(
            conn, "alerta",
            f"Nao consegui transcrever o audio de {lead['nome']}",
            agora, lead["id"],
        )

    seq = fila.chegou(lead["id"], mensagem.imagem_b64, mensagem.media_type)
    if not fila.aguardar(lead["id"], seq, humano.janela_debounce(rng), dormir):
        return  # chegou mensagem mais nova; a rodada dela responde o bloco

    while True:
        if not fila.assumir(lead["id"]):
            return  # rodada ativa reprocessa ao liberar
        try:
            _responder(conn, evo, cliente_claude, cfg, lead, agora, rng,
                       dormir, powercrm, fila, agora_envio)
        finally:
            reprocessar = fila.liberar(lead["id"])
        if not reprocessar:
            return


def _historico_com_midia(conn, lead_id: int, midia) -> list[dict]:
    historico = _historico(conn, lead_id)
    if midia and historico and historico[-1]["direcao"] == "entrada":
        historico[-1]["imagem_b64"], historico[-1]["media_type"] = midia
    return historico


def _responder(conn, evo, cliente_claude, cfg, lead, agora, rng,
               dormir, powercrm, fila, agora_envio) -> None:
    if disjuntor.esta_pausado(conn):
        return  # kill switch: mensagem gravada, robô mudo

    historico = _historico(conn, lead["id"])
    if not historico or historico[-1]["direcao"] == "saida":
        return  # nada novo a responder (bloco já coberto por regeneração)

    if status_de(conn, lead["id"]) == Status.CONTATADO:
        transicionar(conn, lead["id"], Status.EM_CONVERSA, agora)

    status_atual = status_de(conn, lead["id"])
    modelo = (cfg.modelo_fechamento if status_atual in _FASES_DE_FECHAMENTO
              else cfg.modelo_triagem)
    ferramentas = (Ferramentas(conn, powercrm, lead["id"], agora)
                   if powercrm is not None else None)

    dormir(humano.atraso_leitura(rng))
    ultima = conn.execute(
        "SELECT wa_message_id FROM mensagens WHERE lead_id = ? AND "
        "direcao = 'entrada' ORDER BY id DESC LIMIT 1", (lead["id"],)
    ).fetchone()
    if ultima and ultima["wa_message_id"]:
        evo.marcar_lida(lead["telefone_e164"], ultima["wa_message_id"])

    midia = fila.tirar_midia(lead["id"])
    for _ in range(_MAX_REGENERACOES + 1):
        seq = fila.seq_atual(lead["id"])
        historico = _historico_com_midia(conn, lead["id"], midia)
        qualificacao = conversar(cliente_claude, dict(lead), historico,
                                 ferramentas=ferramentas, modelo=modelo)
        if not fila.mudou(lead["id"], seq):
            break  # nada chegou durante a geração; resposta vale

    turnos = lead["turnos"] + 1
    decisao = qualificacao.decisao
    if turnos >= TETO_TURNOS and decisao == "continuar":
        decisao = "escalar"
    if decisao != "opt_out":  # blocklist sempre vence sobre escaladas automáticas
        if ferramentas is not None and ferramentas.falhas_powercrm >= 2:
            decisao = "escalar"
        if ferramentas is not None and ferramentas.escalou:
            decisao = "escalar"

    dormir(humano.atraso_resposta(rng))
    if qualificacao.resposta:
        for parte in humano.quebrar(qualificacao.resposta):
            evo.digitando(lead["telefone_e164"],
                          humano.duracao_digitando(parte, rng))
            dormir(humano.duracao_digitando(parte, rng))
            wa_id = evo.enviar_texto(lead["telefone_e164"], parte)
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, "
                "wa_message_id, criado_em) VALUES (?, 'saida', 'texto', ?, ?, ?)",
                (lead["id"], parte, wa_id, agora_envio().isoformat()),
            )

    conn.execute(
        "UPDATE leads SET turnos = ?, resumo = ?, paga_hoje = ?, "
        "tem_cobertura = ?, carro_quitado = ?, ultimo_evento_em = ? "
        "WHERE id = ?",
        (turnos, qualificacao.resumo, qualificacao.paga_hoje,
         qualificacao.tem_cobertura, qualificacao.carro_quitado,
         agora.isoformat(), lead["id"]),
    )
    conn.commit()

    if ferramentas is not None and ferramentas.fechou:
        handoff.avisar_fechamento(
            conn, evo, cfg.equipe_telefone,
            _lead_por_telefone(conn, lead["telefone_e164"]), agora,
        )

    novo_status = _DECISAO_PARA_STATUS.get(decisao)
    if novo_status is None:
        eventos.registrar(
            conn, "resposta", f"{lead['nome']} respondeu", agora, lead["id"]
        )
        return

    if status_de(conn, lead["id"]) != novo_status:
        transicionar(conn, lead["id"], novo_status, agora)

    if novo_status is Status.OPT_OUT:
        blocklist.bloquear(conn, lead["telefone_e164"], "opt_out", agora)
        eventos.registrar(
            conn, "alerta",
            f"{lead['nome']} pediu opt-out — número na blocklist",
            agora, lead["id"],
        )
    elif novo_status is Status.ESCALADO:
        handoff.avisar_escalada(
            conn, evo, cfg.equipe_telefone, lead, qualificacao.resumo, agora
        )
    else:
        eventos.registrar(
            conn, "sistema", f"{lead['nome']} encerrado como {novo_status}",
            agora, lead["id"],
        )
```

Atenção aos detalhes que mudaram em relação ao código antigo:
- O caso "webhook repetido" (`gravou == False`): não gera resposta (o teste
  `test_mensagem_duplicada_nao_responde_duas_vezes` continua valendo) — o
  bloco do duplicado acima só responde se havia pendência real na fila; na
  prática o caminho normal é `return` imediato.
- `mensagem.telefone` não é mais usado para enviar/marcar lida — usa-se
  `lead["telefone_e164"]` (o JID pode vir sem nono dígito; os testes
  `test_responde_quando_jid_vem_sem_nono_digito` e
  `test_opt_out_bloqueia_o_telefone_do_cadastro` cobrem isso; ajustar seus
  asserts para o E164 do cadastro se necessário).
- `mensagem.imagem_b64` agora viaja pela fila (`chegou`/`tirar_midia`), não
  mais anexada direto ao histórico em `processar`.

Simplificação permitida: se o bloco do "webhook repetido com pendência" 
complicar, trocar por `return` simples quando `gravou == False` — pendência
órfã é coberta pelo laço `assumir/liberar` da rodada que a criou. Nesse caso
remover o teste `test_nao_responde_se_historico_termina_em_saida` da parte
que depende desse caminho e testar o guard de `saida` via pendência do laço.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest tests/ -q`
Expected: PASS — inclusive os testes antigos de `test_resposta.py` (ajustar
chamadas existentes apenas se quebrarem por causa dos novos kwargs; a
assinatura antiga continua válida por serem keyword-only com default)

- [ ] **Step 5: Commit**

```bash
git add src/disparo/resposta.py tests/test_resposta.py
git commit -m "feat: resposta em bloco com debounce, regeneracao e hora real de envio"
```

---

### Task 4: Ligar a fila compartilhada no app e webhook

**Files:**
- Modify: `src/disparo/app.py:39-57` (montar_estado)
- Modify: `src/disparo/webhook.py:21-25`
- Test: `tests/test_webhook.py`

**Interfaces:**
- Consumes: `FilaPorLead` (Task 1); `processar(..., fila=...)` (Task 3).
- Produces: `estado.fila` disponível para todo o app.

- [ ] **Step 1: Teste**

Em `tests/test_webhook.py`, garantir que o estado fake ganhe `fila` e que o
`add_task` repasse `fila=estado.fila` (seguir o padrão dos testes existentes
do arquivo; se eles inspecionam os args da task agendada, acrescentar o
assert do kwarg `fila`):

```python
def test_webhook_repassa_a_fila_do_estado(...):
    # no estado fake: fila=FilaPorLead()
    # assert: kwargs da task contêm fila is estado.fila
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_webhook.py -q`
Expected: FAIL

- [ ] **Step 3: Implementar**

`app.py` — em `montar_estado`, acrescentar ao `SimpleNamespace`:

```python
from disparo.fila import FilaPorLead
# ...
        fila=FilaPorLead(),
```

`webhook.py` — repassar a fila:

```python
        tarefas.add_task(
            processar, estado.conn, estado.evo, estado.claude, estado.cfg,
            mensagem, datetime.now(), estado.rng, estado.dormir,
            getattr(estado, "powercrm", None),
            fila=estado.fila,
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/disparo/app.py src/disparo/webhook.py tests/test_webhook.py
git commit -m "feat: fila compartilhada por lead no estado do app"
```

---

### Task 5: Prompt adaptativo e aberturas variadas em `conversador.py`

**Files:**
- Modify: `src/disparo/conversador.py:14-74` (ABERTURAS e PROMPT)
- Test: `tests/test_conversador.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (independente das Tasks 1–4).
- Produces: `ABERTURAS` com 12 variações; `PROMPT` novo. Assinaturas de
  `abertura`, `conversar` e o schema `Qualificacao` NÃO mudam.

- [ ] **Step 1: Testes**

Em `tests/test_conversador.py`, acrescentar (e ajustar asserts antigos que
citem texto do prompt velho, se existirem):

```python
def test_aberturas_tem_12_variacoes_unicas_com_nome():
    assert len(ABERTURAS) >= 12
    assert len(set(ABERTURAS)) == len(ABERTURAS)
    assert all("{nome}" in a for a in ABERTURAS)
    assert all(not a.endswith(".") for a in ABERTURAS)


def test_prompt_tem_regras_duras_e_anti_repeticao():
    assert "Nunca invente preço" in PROMPT
    assert "NUNCA repita" in PROMPT
    assert "não é roteiro fixo" in PROMPT
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_conversador.py -q`
Expected: FAIL

- [ ] **Step 3: Implementar**

Substituir `ABERTURAS`:

```python
ABERTURAS = (
    "Oii {nome}, tudo bem?",
    "Oi {nome}, tudo bem?",
    "Bom dia {nome}, tudo bem?",
    "Oi {nome}, tudo certo?",
    "Opa {nome}, tudo bem?",
    "Oi {nome}, como vai?",
    "{nome}, tudo bem com você?",
    "Oii {nome}, tudo certo por aí?",
    "Oi {nome}, tudo joia?",
    "Olá {nome}, tudo bem?",
    "Oi {nome}, beleza?",
    "Bom dia {nome}, tudo certo?",
)
```

Substituir `PROMPT` inteiro por:

```python
PROMPT = """\
Você conversa por WhatsApp em nome da Porto Sul, empresa de proteção veicular.
Objetivo: despertar interesse, montar a cotação e fechar a venda, conduzindo
da primeira mensagem até o fechamento como uma vendedora humana faria.

# Caminho de referência (não é roteiro fixo)
Estes passos são o caminho que costuma funcionar. Siga a ordem quando a
conversa fluir natural, mas pule, junte ou reordene conforme o lead: quem já
pergunta preço vai direto pra cotação; quem diz que tem seguro vai direto pra
comparação; quem responde seco recebe menos conversa e mais objetividade.
1. Confirmar o veículo: algo como "vi aqui que você tem um {veiculo}, certo?"
   - Se não for o carro dele, peça desculpa e encerre com decisao=dado_desatualizado.
2. Quebra-gelo leve (ex.: perguntar se ele passa pretinho no pneu) — só se o
   clima da conversa permitir.
3. Identificação: você trabalha na Porto Sul, de proteção veicular; associado
   ganha pretinho e cheirinho de graça de 6 em 6 meses.
4. Oferta: o principal é a proteção, que costuma sair bem abaixo de seguro.
   Descubra aos poucos: o carro tem proteção hoje? quanto paga por mês? é
   quitado ou financiado?
5. Fechamento: ofereça montar a cotação, sem compromisso.
6. Peça a placa do veículo para puxar o valor exato.
7. Com a placa, use a ferramenta cotar. Apresente mensalidade e adesão direto,
   sem floreio. Não afirme nada além do que a ferramenta devolver.
8. Objeção: contorne no máximo 2 vezes, com argumento (custo de ficar sem
   proteção, aceitação de perfil que seguradora recusa).
9. Aceite: depois do sim explícito, use fechar_venda, avise que a equipe manda
   o boleto aqui na conversa em instantes e que dá pra pagar por PIX no
   próprio boleto. Encerre educadamente e aguarde.

# Como escrever
Escreva como uma pessoa de verdade no WhatsApp, não como atendente de script:
- Frases curtas, no máximo duas linhas. Português correto, tom informal.
- Sem ponto final em mensagem curta ("tudo certo", não "Tudo certo.").
- NUNCA repita uma frase que você já enviou nesta conversa — reformule com
  outras palavras, mesmo que o lead repita a pergunta ou a saudação.
- Espelhe o registro do lead: seco com quem é seco, descontraído com quem
  brinca. Voz feminina — concordância no feminino e "obrigada".
- Pode usar "tranquilo", "boa", "haha", "perfeito". Nada de gíria pesada
  ("salve", "suave", "firmeza", "mano", "top") nem formalidade ("prezado",
  "venho por meio desta").
- Sem emoji nas duas primeiras mensagens; depois no máximo um, e só se o lead
  usar primeiro. Sem caixa alta e sem exclamação dupla.
- Se o lead mandou várias mensagens seguidas, responda tudo de uma vez numa
  resposta só, coerente — não responda item por item como robô.

# Regras duras (nunca quebre, em nenhuma hipótese)
- Nunca invente preço, mensalidade, adesão ou desconto. Valor só o que a
  ferramenta cotar devolver. Quem negocia valor é a vendedora.
- Nunca prometa cobertura nem afirme o que está incluso.
- NUNCA ofereça desconto; se o lead insistir em desconto, use escalar_humano.
- Qualquer pedido para parar de receber mensagem: decisao=opt_out, imediato.
- Se perguntarem se você é robô, bot ou automação, diga a verdade: a primeira
  abordagem é automatizada e a vendedora assume em seguida. Nunca negue.
- Nunca insista depois de uma recusa clara.
- Só comente que o valor que ele paga é alto se for realmente alto; valor
  normal recebe resposta neutra.
- fechar_venda só depois de um sim explícito ("fecho", "pode mandar",
  "aceito").

# Classificação
decisao=frio quando responde sem interesse ou já está satisfeito com o que tem.
decisao=opt_out em qualquer pedido para parar de receber mensagem.
decisao=dado_desatualizado quando o veículo não é dele.
decisao=escalar quando você usou escalar_humano ou a conversa precisa de gente.
decisao=continuar no resto — inclusive durante toda a fase de fechamento.

O campo resposta é exatamente o texto que será enviado ao lead.
"""
```

- [ ] **Step 4: Rodar a suíte inteira**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/disparo/conversador.py tests/test_conversador.py
git commit -m "feat: prompt adaptativo com estilo natural e 12 aberturas"
```

---

### Task 6: Deploy no VPS e retomada da conversa de teste

**Files:** nenhum (operação). Requer o ssh do Windows (`/c/Windows/System32/OpenSSH/ssh.exe`, host `crm-vps`) — o ssh do Git Bash não enxerga o agent com a chave.

- [ ] **Step 1: Suíte completa local**

Run: `python -m pytest tests/ -q`
Expected: PASS (todos)

- [ ] **Step 2: Push**

```bash
git push origin main
```

- [ ] **Step 3: Deploy**

```bash
/c/Windows/System32/OpenSSH/ssh.exe crm-vps 'cd /opt/disparo && git pull && docker compose up -d --build'
```

- [ ] **Step 4: Verificar saúde**

```bash
/c/Windows/System32/OpenSSH/ssh.exe crm-vps 'sleep 5 && curl -s http://127.0.0.1:8010/saude'
```

Expected: `{"ok":true}`

- [ ] **Step 5: Retomar a conversa com o lead de teste**

A conversa do lead 1 (5537991048239) parou na nossa pergunta sem resposta.
Enviar UMA mensagem de retomada natural pela Evolution E gravá-la no banco
(direcao=saida), via script dentro do container:

```bash
/c/Windows/System32/OpenSSH/ssh.exe crm-vps 'docker exec disparo python - <<PYEOF
from datetime import datetime
import httpx
from disparo.config import carregar_config
from disparo.db import conectar
from disparo.evolution import Evolution

cfg = carregar_config()
conn = conectar(cfg.db)
evo = Evolution(cfg.evolution_base_url, cfg.evolution_api_key,
                cfg.evolution_instance, httpx.Client(timeout=30))
texto = "oi Yuri, tudo bem? conseguiu ver minha ultima mensagem?"
wa_id = evo.enviar_texto("5537991048239", texto)
conn.execute(
    "INSERT INTO mensagens (lead_id, direcao, tipo, texto, wa_message_id, criado_em) "
    "VALUES (1, 'saida', 'texto', ?, ?, ?)",
    (texto, wa_id, datetime.now().isoformat()))
conn.commit()
print("enviado", wa_id)
PYEOF'
```

- [ ] **Step 6: Observar a resposta ao vivo**

Usuário responde do celular; acompanhar:

```bash
/c/Windows/System32/OpenSSH/ssh.exe crm-vps 'docker logs disparo --tail 50 -f'
```

Conferir no banco: uma resposta só por bloco, atraso 15–60s entre `criado_em`
da entrada e da saída, sem frase repetida verbatim.

---

## Self-review (feito na escrita)

- Cobertura do spec Seções 1–2: debounce (T1/T3), trava (T1/T3), checagem
  pré-envio (T3), timestamps reais (T3), tempo 15–60s (T2), prompt adaptativo
  + regras duras + anti-repetição + 12 aberturas (T5), fiação (T4), deploy e
  teste ao vivo (T6). Seção 3 fora deste plano, por decisão registrada.
- Tipos consistentes: `FilaPorLead` da T1 é a mesma consumida em T3/T4;
  kwargs de `processar` batem entre T3 e T4.
