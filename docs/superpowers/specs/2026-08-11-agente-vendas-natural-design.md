# Agente de vendas natural — design

Data: 2026-08-11
Status: aprovado pelo usuário (conversa de brainstorming)

## Contexto

Teste real da conversa (lead 1) expôs três defeitos de mecânica e um prompt
rígido demais:

1. Mensagens rápidas consecutivas ("Sim" + "Por que?]" com 2s de intervalo)
   geraram dois processamentos paralelos e duas respostas sobrepostas — o
   webhook agenda um `processar` por mensagem, sem trava por lead.
2. Resposta gravada com a hora de chegada da mensagem de entrada
   (`resposta.py` usa `agora` no INSERT de saída), o que esconde os atrasos
   humanos reais e atrapalha qualquer análise.
3. O modelo repetiu uma frase idêntica quando o lead repetiu "Oi" — nada
   impede repetição verbatim.
4. O roteiro fixo de 9 etapas trava a conversa em sequência única; lead que
   foge do trilho recebe resposta engessada, com cara de robô.

Duas mensagens ignoradas no teste (20:42/20:52 de 2026-08-10) foram causadas
por bugs já corrigidos (`3a8c3aa`, `3fc05f7`) e não fazem parte deste design.

## Objetivo

Conversa indistinguível de um humano atento: uma resposta coerente por bloco
de mensagens, tempo de resposta 15–60s, fraseado variado (anti-bloqueio do
WhatsApp), condução adaptativa por lead, e melhoria contínua supervisionada
a partir dos gargalos reais.

## Seção 1 — Mecânica da conversa

### Fluxo novo do webhook

- O webhook grava a mensagem de entrada no banco imediatamente (dedup por
  `wa_message_id` mantido) e agenda a resposta via fila por lead. Hoje a
  gravação acontece dentro de `processar`; ela sobe para o caminho do webhook.
- **Debounce por lead (8–20s, aleatório)**: a resposta só é gerada depois de
  8–20s sem mensagem nova daquele lead. Mensagem nova dentro da janela entra
  no bloco e reinicia o relógio. Uma única resposta cobre o bloco inteiro.
- **Trava por lead**: nunca há dois processamentos simultâneos do mesmo lead.
  Mensagem que chega durante um processamento em andamento agenda nova rodada
  ao final (não é descartada).
- **Checagem pré-envio**: depois que o modelo gera a resposta, se chegou
  mensagem nova do lead nesse meio-tempo, a resposta é descartada e o bloco é
  reprocessado com o histórico completo — no máximo 2 regenerações; na
  terceira, envia mesmo assim.
- **Tempo percebido total** (debounce + atraso de leitura + "digitando…"):
  ~15–60s, aleatório. `humano.atraso_resposta` deixa de somar 15–180s por
  cima; os componentes são recalibrados para o total cair nessa faixa.
- **Timestamp real**: cada mensagem de saída é gravada com a hora do envio
  de fato (`datetime.now()` no momento do INSERT), não com a hora de chegada
  da entrada.

### Fora do escopo

- Persistir a fila de debounce entre restarts: mensagem já está no banco;
  se o serviço reiniciar dentro da janela, o lead fica sem resposta até a
  próxima mensagem dele. Aceito (janela de segundos, risco mínimo).

## Seção 2 — Agente vendedor

### Prompt: de roteiro a guia adaptativo

- As 9 etapas viram um **caminho de referência**, não uma sequência
  obrigatória: o agente pula, reordena e adapta conforme o comportamento do
  lead (ex.: lead que já pergunta preço vai direto para cotação).
- **Regras duras imutáveis** (bloco separado no prompt): nunca inventar
  preço, valor ou desconto; nunca prometer ou afirmar cobertura; nunca
  oferecer desconto; opt-out imediato a qualquer pedido de parar; nunca negar
  que a primeira abordagem é automatizada se perguntado; nunca insistir após
  recusa.
- **Estilo natural**: informal correto, sem ponto final em mensagens curtas,
  frases do jeito que gente escreve no WhatsApp, espelha o registro do lead
  (seco com seco, descontraído com descontraído). Sem gíria pesada, sem
  formalidade, sem caixa alta, emoji só se o lead usar primeiro.
- **Anti-repetição**: proibido repetir frase já enviada na conversa —
  reformular sempre. O histórico completo já vai ao modelo; a instrução
  explícita entra no prompt.
- **Aberturas**: de 3 para ~12 variações com estruturas diferentes entre si
  (não só o cumprimento trocado), para descaracterizar padrão de disparo.
- Modelos mantidos: Haiku na triagem, Sonnet no fechamento (config atual).

## Seção 3 — Auto-aprendizado supervisionado

### Coleta de gargalos

Job diário no agendador existente seleciona conversas com sinal de gargalo:

- Lead sumiu: >24h sem resposta dele após mensagem nossa, status ainda ativo.
- Esfriou: decisao=frio.
- Escalada precoce: escalado com 4 turnos ou menos.
- Opt-out.

### Análise e sugestão

- Claude (Sonnet) analisa o lote de conversas-gargalo e produz sugestões
  concretas de ajuste no direcionamento, cada uma com diagnóstico e texto
  proposto (ex.: "40% somem após a pergunta do pretinho — trocar por X").
- Sugestões gravadas em tabela nova `aprendizado` com status `pendente`.

### Aprovação e aplicação

- Painel ganha seção de aprendizado: pendentes com **aprovar / rejeitar**;
  ativas com **desativar**.
- Sugestão aprovada vira diretriz num bloco "Aprendizados aprovados"
  injetado no system prompt do conversador.
- Nada muda sem aprovação; desativar reverte na hora.

## Ordem de entrega

1. Seção 1 (mecânica) → 2. Seção 2 (prompt) → deploy → teste ao vivo com o
   número 5537991048239 (retomar a conversa do lead 1) → 3. Seção 3
   (aprendizado).

## Testes

- Debounce: mensagens consecutivas dentro da janela geram uma única resposta;
  fora da janela, respostas separadas; relógio reinicia a cada mensagem nova.
- Trava: mensagem durante processamento agenda nova rodada, nunca roda em
  paralelo.
- Checagem pré-envio: mensagem nova durante a geração descarta e regenera;
  limite de 2 regenerações.
- Timestamp de saída = hora do envio.
- Tempo percebido dentro de 15–60s.
- Prompt: casos de condução adaptativa (lead pede preço de cara, lead seco,
  lead repete saudação) sem repetição verbatim.
- Aprendizado: job coleta gargalos certos, sugestão pendente não afeta o
  prompt, aprovada entra, desativada sai.
