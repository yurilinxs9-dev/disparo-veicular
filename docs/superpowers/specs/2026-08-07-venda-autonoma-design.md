# Venda autônoma — Etapa 2 — Porto Sul proteção veicular

**Data:** 2026-08-07
**Status:** especificação aprovada em conversa (abordagem A); aguardando revisão do documento
**Base:** evolui o serviço da Etapa 1 (`docs/superpowers/specs/2026-08-04-disparo-qualificacao-design.md`), já em produção no VPS.

## Problema

A Etapa 1 qualifica o lead e entrega o quente à vendedora. A Porto Sul quer que a IA
feche a venda sozinha: cotar, apresentar preço, contornar objeção, cobrar e só então
acionar gente — exclusivamente para a vistoria do veículo.

Referência de mercado estudada: venda.ai (agentes de venda no WhatsApp para
e-commerce, R$ 397–597/mês). Ela mesma não atende venda de serviços; por isso a
solução é própria, sob medida e mais enxuta.

## Decisões tomadas

| Decisão | Escolha | Motivo |
|---|---|---|
| Alcance da autonomia | Cotação + fechamento total; humano só na vistoria | Decidido pelo dono. |
| Fonte de preço | Power CRM via Power API (token Bearer) | É onde a Porto Sul já cota; API tem cotação, seleção de plano e consulta por placa. |
| Desconto | Nenhum. Preço de tabela; objeção se contorna com argumento | Simples, sem risco de a IA corroer margem. |
| Pagamento | Boleto (pagável via PIX) criado no Power CRM e enviado na conversa | Financeiro fica no sistema que a empresa já opera. |
| Vistoria | Após pagamento, IA envia resumo ao WhatsApp da equipe, que agenda | Sem integração de agenda; mesmo mecanismo do handoff atual. |
| Arquitetura | Conversador único ganha ferramentas (tool use) | Evolução direta do código existente; máquina de estados e pipeline de resposta continuam os mesmos. |
| Modelos | `claude-haiku-4-5` na triagem; `claude-sonnet-5` da cotação em diante | Negociação pede mais capacidade; custo estimado US$ 60–90/mês, ~10x abaixo da venda.ai. |
| Becos sem saída | Handoff humano, como hoje | Recusa firme, pedido fora de escopo ou falha repetida do Power CRM nunca travam o lead. |

## Fluxo completo

```
qualificação (Etapa 1, Haiku)
      │ lead esquenta
      ▼
IA pede placa (e dados que a adesão exigir)
      ▼
[cotar] Power CRM ──► plano + mensalidade + adesão
      ▼
IA apresenta preço de tabela (Sonnet)
      │ objeção → contorna com argumento (máx. 2 tentativas)
      │ aceite explícito
      ▼
[gerar_cobranca] Power CRM ──► boleto/PIX na conversa
      ▼                         status: aguardando_pagamento
webhook Power CRM confirma pagamento
      ▼                         status: pago
IA parabeniza o cliente; equipe recebe resumo para agendar vistoria
```

## Componentes

### 1. Cliente Power CRM (`powercrm.py`)

**Faz:** fala com a Power API usando token Bearer.

**Produz:**
- `cotar(dados_lead) -> Cotacao` — placa/modelo do veículo entram, sai
  `Cotacao(cotacao_id, plano, mensalidade, adesao)`.
- `gerar_cobranca(cotacao_id, dados_adesao) -> Cobranca` —
  `Cobranca(cobranca_id, link_boleto, pix_copia_cola)`.

**Erros:** hierarquia própria (`PowerCRMErro`, `PowerCRMIndisponivel`,
`PowerCRMRecusa`) espelhando o padrão do cliente da Evolution. Campos exatos dos
endpoints saem da documentação logada da Porto Sul (pendência externa); o cliente
isola esse conhecimento num único arquivo.

### 2. Conversador com ferramentas (`conversador.py` evoluído)

O conversador deixa de ser uma chamada única de saída estruturada e vira um laço de
tool use. Ferramentas expostas ao modelo:

- `cotar` — chama o cliente Power CRM; devolve preço ao modelo.
- `gerar_cobranca` — só funciona se já existe cotação aceita; devolve link.
- `escalar_humano` — encerra a autonomia e aciona o handoff.

A saída estruturada continua existindo ao fim de cada turno, com `decisao` ampliada:
`continuar | frio | opt_out | dado_desatualizado | aguardando_pagamento | escalar`.
O valor `quente` da Etapa 1 deixa de encerrar a conversa: esquentar agora significa
entrar na fase de fechamento.

**Troca de modelo:** turnos com status até `em_conversa` usam Haiku; a partir de
`negociando`, Sonnet. Um campo no config define os dois IDs.

**Teto de turnos:** sobe de 12 para 20; estourou sem fechar → `escalar`.

### 3. Máquina de estados (novos status)

```
em_conversa ──► negociando ──► aguardando_pagamento ──► pago (terminal)
     │               │                  │
     └───────────────┴──────────────────┴──► escalado (terminal, humano assume)
```

- `negociando`: cotação apresentada.
- `aguardando_pagamento`: boleto enviado. Lembrete único após 48h sem pagamento;
  às 72h vira `escalado` com aviso à equipe.
- `pago`: webhook confirmou. Dispara aviso de vistoria à equipe.
- `escalado`: substitui o papel do `quente` terminal da Etapa 1 — robô silencia,
  humano assume pelo WhatsApp Web.

Status da Etapa 1 (`frio`, `opt_out`, `sem_resposta`, `invalido`,
`dado_desatualizado`) seguem intactos.

### 4. Webhook de pagamento (`/webhook/powercrm`)

Endpoint novo no FastAPI. Power Webhook (Minha Empresa → Integrações) aponta para
ele com token próprio no header; requisições sem o token são recusadas. Evento de
pagamento confirmado: localiza o lead pela cobrança, transiciona para `pago`,
envia mensagem de boas-vindas ao cliente e o resumo de vistoria à equipe.
Idempotente como o webhook da Evolution (cobrança já paga é ignorada).

### 5. Roteiro da fase de fechamento (prompt)

1. Confirmar interesse e pedir placa (+ dados exigidos pela adesão).
2. Cotar e apresentar: mensalidade + adesão, cobertura resumida, sem floreio.
3. Objeção: no máximo 2 contornos com argumento (custo x prejuízo, aceitação de
   perfil que seguradora recusa). Nunca oferecer desconto.
4. Aceite explícito ("fecho", "pode mandar") antes de gerar cobrança — a IA nunca
   cobra sem sim claro.
5. Enviar boleto com instrução de pagamento via PIX.
6. Silenciar até o webhook ou o lembrete de 48h.

### 6. Painel

- Filtros ganham os novos status.
- Cabeçalho ganha o funil do dia: cotados / boletos enviados / pagos.
- Sem tela nova; mesmo HTML, mesmos endpoints com campos a mais.

### 7. Configuração nova

```
POWERCRM_BASE_URL=
POWERCRM_TOKEN=
POWERCRM_WEBHOOK_TOKEN=
EQUIPE_TELEFONE=          # avisos de vistoria/escalada; default: VENDEDORA_TELEFONE
MODELO_TRIAGEM=claude-haiku-4-5
MODELO_FECHAMENTO=claude-sonnet-5
```

## Proteções e conformidade

- Disjuntor, cota, janela e blocklist da Etapa 1 seguem valendo — fechamento é
  resposta a mensagem recebida, não disparo, então não consome cota.
- CPF e dados de adesão: coletados só se a API exigir, guardados no SQLite local
  (mesmo banco, coluna nova), nunca em log.
- Falha do Power CRM no meio da conversa: IA responde que confirma o valor em
  instantes, evento `alerta` no painel; segunda falha seguida → `escalar`.
- Kill switch pausa também respostas da fase de fechamento (hoje só pausa disparo;
  passa a cobrir a conversa quando pausado).

## Fora do escopo

- Desconto ou alçada de negociação de preço.
- Agendamento de vistoria com calendário integrado.
- Follow-up de inadimplência além do lembrete único de 48h.
- Múltiplos planos/upsell: a IA apresenta o plano que o Power CRM devolver para o
  perfil.

## Pendências externas (bloqueiam implementação)

1. Porto Sul gerar o token da Power API e enviar (Minha Empresa → Integrações →
   Power API → gerar token).
2. Print/export da documentação de endpoints que abre logado (campos exatos de
   cotação e adesão).
3. Instância da Evolution do chip da Porto Sul (Etapa 1 também espera por ela).

## Testes

Mesmo padrão da Etapa 1: cliente Power CRM falso nos testes de conversa e resposta;
respx para o cliente HTTP real; webhook de pagamento testado com TestClient,
incluindo token errado e evento repetido. Laço de tool use testado com cliente
Claude falso que devolve chamadas de ferramenta roteirizadas.
