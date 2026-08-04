# Disparo e qualificação de leads — proteção veicular

**Data:** 2026-08-04
**Status:** especificação aprovada para planejamento

## Problema

Uma empresa de proteção veicular mantém listas de contatos (nome, telefone, veículo) e hoje aborda cada pessoa manualmente pelo WhatsApp. O processo não escala: o vendedor gasta o dia abrindo conversa e fazendo as mesmas três perguntas de triagem, e só uma fração dos contatos tem interesse real.

O objetivo é automatizar a abertura da conversa e a qualificação, entregando ao vendedor apenas os leads que valem atendimento humano.

## Escopo

**Dentro do escopo:**

- Importação de listas em CSV.
- Disparo da mensagem inicial pelo WhatsApp, com limite de 30 contatos por dia.
- Conversa de qualificação conduzida por IA (até três perguntas).
- Classificação do lead e transferência para o vendedor quando o lead está quente.
- Proteções contra bloqueio do número e conformidade com opt-out.
- Painel web de monitoramento, somente leitura.

**Fora do escopo:**

- Atendimento humano dentro do sistema. O vendedor responde pelo WhatsApp Web do próprio chip.
- Envio de propostas, cotações ou documentos.
- Integração com o CRM existente (projeto separado).
- Múltiplos atendentes ou múltiplos números.

## Decisões tomadas

| Decisão | Escolha | Motivo |
|---|---|---|
| Formato | Serviço único em Python | A qualificação é uma máquina de estados com histórico por lead; isso é mais curto, testável e depurável em código do que em fluxo visual. |
| WhatsApp | Evolution API (não oficial, self-hosted) no VPS existente | Já operada no VPS; sem custo por mensagem. Migração para a API oficial fica registrada como evolução futura. |
| Modelo de IA | `claude-haiku-4-5` | Três perguntas fixas de triagem não exigem mais capacidade. Custo estimado de US$ 9/mês para 900 leads. |
| Banco | SQLite (arquivo único) | Volume de centenas de leads; backup é copiar um arquivo. |
| Número | Chip dedicado, já aquecido | Isola o risco de bloqueio do número principal da empresa. |
| Handoff | Aviso ao WhatsApp do vendedor; bot silencia o lead | Uma pessoa atende; painel de atendimento seria custo sem retorno. |
| Painel | Somente leitura, com kill switch e importação de CSV | Observabilidade sem duplicar o WhatsApp. |

## Arquitetura

```
CSV da empresa
    │
    ▼
[1] Importador ──► SQLite (leads)
                      │
                      ▼
                 [2] Agendador ──► Evolution API ──► WhatsApp (chip dedicado)
                  (janela, cota,                          │
                   intervalo)                             │ lead responde
                      ▲                                   ▼
                      │                          [3] Webhook receiver
                      │                                   │
                      │                                   ▼
                      └───── status ◄──── [4] Qualificador (Haiku)
                                                          │
                                                 lead quente
                                                          ▼
                                             [5] Aviso ao vendedor

                 [6] Painel de monitoramento (leitura + kill switch)
```

Cada componente tem uma responsabilidade única e um contrato explícito, para poder ser entendido e testado isoladamente.

### 1. Importador

**Faz:** lê um CSV e grava leads novos no banco.

**Entrada:** arquivo CSV com pelo menos nome, telefone e veículo. Colunas extras são ignoradas.

**Regras:**

- Normaliza o telefone para E.164 (`5511987654321`). Descarta o que não normalizar.
- Descarta duplicatas dentro do arquivo e contra leads já existentes.
- Descarta qualquer telefone presente na blocklist.
- Grava cada lead restante com status `novo`.

**Saída:** relatório de importação — total lido, importado, duplicado, inválido, bloqueado.

### 2. Agendador

**Faz:** decide quando e para quem enviar a mensagem inicial.

**Roda:** a cada minuto.

**Só envia se todas forem verdadeiras:**

- Sistema não está pausado.
- Horário dentro da janela (padrão 09:00–18:00, segunda a sexta).
- Cota do dia não esgotada.
- Intervalo desde o último envio já cumprido (sorteado entre 2 e 8 minutos).
- Disjuntor não disparou.

**Antes de cada envio:** consulta a Evolution para confirmar que o número existe no WhatsApp. Se não existir, marca o lead como `invalido`, não consome cota e passa ao próximo.

**Mensagem inicial:** montada a partir de uma de três variações de texto, com nome e veículo do lead. Sem links, sem imagens, sem blocos longos.

**Após enviar:** lead vai para `contatado`, cota do dia incrementa, evento gravado.

### 3. Webhook receiver

**Faz:** recebe as mensagens que chegam da Evolution.

**Regras:**

- Grava a mensagem e devolve 200 imediatamente. Nenhum processamento pesado no handler.
- `wa_message_id` é UNIQUE — reenvio do mesmo webhook é ignorado silenciosamente.
- Mensagem de lead em estado `quente`, `frio`, `opt_out` ou `sem_resposta` é apenas gravada; o robô não responde.
- Caso contrário, dispara o qualificador.

### 4. Qualificador

**Faz:** conduz a conversa e classifica o lead.

**Perguntas** (no máximo três, uma por vez, em ordem natural — o modelo pula o que o lead já respondeu):

1. Tem seguro ou proteção hoje? Se sim, quanto paga por mês?
2. O carro é quitado ou financiado?
3. Quer receber uma cotação?

**Chamada:** uma por mensagem recebida, com o histórico da conversa e saída estruturada (`output_config.format`), para que a decisão venha tipada em vez de exigir interpretação de texto livre. O prompt do sistema é fixo e recebe `cache_control` para aproveitar o cache de prefixo.

**Retorno:**

| Campo | Descrição |
|---|---|
| `resposta` | Texto a enviar ao lead |
| `decisao` | `continuar`, `quente`, `frio` ou `opt_out` |
| `resumo` | Uma linha para o vendedor |
| `paga_hoje` | Valor mensal atual, se informado |
| `tem_cobertura` | `sim`, `nao` ou `nao_informado` |

**Critério de lead quente** — qualquer um destes:

- Paga por cobertura hoje e demonstra achar caro.
- Não tem cobertura e demonstra interesse.
- Pede cotação, valor ou mais informação.
- Teve seguro recusado ou caro por perfil (motorista de aplicativo, carro antigo, idade).

**Teto:** seis mensagens do robô por lead. Atingido o teto sem decisão, o lead é marcado `quente` e passa ao humano — um handoff a mais é preferível a um robô conversando indefinidamente.

**Regras do prompt:** nunca fingir ser humano se perguntado diretamente; nunca inventar preço; nunca prometer cobertura; nunca insistir após recusa.

### 5. Handoff

Quando o lead vira `quente`, o sistema envia uma mensagem ao WhatsApp do vendedor com nome, telefone, veículo, resumo, a última fala do lead e o link `wa.me`. O robô silencia aquele número em definitivo, para não atropelar o atendimento humano.

### 6. Painel de monitoramento

Somente leitura, exceto por dois controles: kill switch e importação de CSV.

**Mostra:**

- Estado do sistema, chip conectado e relógio.
- Cota do dia com a posição na rampa.
- Contagem regressiva para o próximo disparo e a janela vigente.
- Disjuntor com os três limiares e seus valores atuais.
- Taxa de resposta dos últimos 14 dias.
- Tabela de leads com filtro por status e busca por nome ou telefone.
- Conversa completa de um lead, com a leitura do qualificador.
- Feed de eventos em tempo real (SSE).

**Acesso:** o painel expõe dados pessoais de leads e o kill switch da operação. Fica atrás de autenticação (senha única do operador) e de HTTPS, nunca aberto na internet sem proteção.

Protótipo visual aprovado em 2026-08-04: https://claude.ai/code/artifact/2e704d95-cfff-465d-8102-d6f8d568b41a

## Proteções contra bloqueio do número

O objetivo é reduzir os sinais de comportamento que levam a bloqueio — denúncias, bloqueios de usuário, baixa taxa de resposta, volume anômalo. Nenhuma proteção depende de mascarar a automação perante a plataforma.

| # | Proteção | Regra |
|---|---|---|
| 1 | Rampa de volume | Dias 1–3: 10/dia. Dias 4–7: 20/dia. Dia 8 em diante: 30/dia. Contada a partir do início da operação. |
| 2 | Janela | 09:00–18:00, segunda a sexta. Configurável. |
| 3 | Ritmo | Intervalo sorteado de 2 a 8 minutos entre envios. |
| 4 | Verificação prévia | Número inexistente no WhatsApp é descartado sem consumir cota. |
| 5 | Personalização | Nome e veículo em toda mensagem inicial; três variações de texto. |
| 6 | Opt-out | Recusa em qualquer forma gera confirmação educada, status `opt_out` e blocklist permanente, consultada em toda importação futura. |
| 7 | Disjuntor | Pausa automática se, nos últimos 50 disparos: taxa de resposta abaixo de 10%, ou 3 ou mais opt-outs, ou qualquer falha de envio por bloqueio. Só volta com destravamento manual. |
| 8 | Sem insistência | Um disparo por lead. Sem resposta em 72h, o lead é encerrado como `sem_resposta`. Nenhum follow-up. |
| 9 | Kill switch | Pausa global imediata pelo painel. |

**Fora do sistema, responsabilidade da empresa:** origem das listas e base legal sob a LGPD. O sistema preserva o histórico para atender pedidos de acesso ou exclusão, mas não decide sobre eles.

## Estados do lead

```
novo ──► contatado ──► em_conversa ──┬──► quente ──► (humano assume)
  │           │                      ├──► frio
  │           │                      └──► opt_out
  │           └──► sem_resposta (72h sem retorno)
  └──► invalido (número não existe no WhatsApp)
```

O robô só fala com leads em `contatado` ou `em_conversa`.

## Modelo de dados

| Tabela | Campos principais |
|---|---|
| `leads` | id, nome, telefone_e164 (UNIQUE), veiculo, status, resumo, tem_cobertura, paga_hoje, turnos, criado_em, contatado_em, ultimo_evento_em |
| `mensagens` | id, lead_id, direcao, texto, wa_message_id (UNIQUE), criado_em |
| `blocklist` | telefone_e164 (PK), motivo, criado_em |
| `envios_diarios` | data (PK), quantidade |
| `eventos` | id, tipo, lead_id, texto, criado_em |
| `config` | chave (PK), valor |

A cota do dia vive em `envios_diarios`, não em memória, para sobreviver a reinícios.

## Tratamento de falhas

| Falha | Comportamento |
|---|---|
| Evolution indisponível | Três tentativas com backoff exponencial. Persistindo, envio fica pendente, cota não é consumida e o operador é avisado. |
| Webhook duplicado | Ignorado pela restrição UNIQUE em `wa_message_id`. |
| API do Claude com erro ou limite | Retry do SDK. Persistindo, o lead permanece na fila e o operador é avisado. O robô nunca responde sem resposta válida do modelo. |
| Resposta do modelo fora do schema, ou recusa | Lead passa ao humano; o robô fica calado. |
| Número inexistente no WhatsApp | Status `invalido`, sem consumo de cota. |
| Reinício do serviço | Todo o estado está em SQLite; o agendador recalcula cota e janela ao subir. |
| Perda de dados | Backup diário do arquivo SQLite. |

## Testes

**Cobertos por teste automatizado:**

- Transições da máquina de estados, incluindo as proibidas.
- Cota diária e rampa, incluindo virada de dia.
- Janela horária, incluindo fim de semana e borda de horário.
- Detecção de opt-out e efeito na blocklist.
- Importador: normalização E.164, deduplicação, respeito à blocklist.
- Disjuntor: cada um dos três limiares.
- Idempotência do webhook.

**Qualificador:** testado com conversas gravadas como fixtures e o cliente do Claude mockado, verificando a decisão para cada cenário (quente por preço, quente por recusa de seguradora, frio, opt-out, continuar). Um teste opcional contra a API real, fora da suíte padrão.

## Custo operacional estimado

| Item | Mensal |
|---|---|
| API do Claude (900 leads, Haiku 4.5, com cache) | ~US$ 9 (~R$ 50) |
| VPS | já existente |
| Chip dedicado | R$ 0–50 |

## Evolução futura, fora deste escopo

- **API oficial do WhatsApp Business** (Meta Cloud API ou provedor). Elimina o risco de bloqueio por automação, ao custo de aprovação de template pela Meta e taxa por conversa (~R$ 300–500/mês neste volume). Exige base com opt-in. A arquitetura isola o envio em uma camada própria justamente para permitir essa troca sem reescrever o resto.
- Segmentação de listas por temperatura ou origem.
- Múltiplos atendentes com distribuição de leads.
