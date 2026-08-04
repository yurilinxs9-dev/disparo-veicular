# Disparo e qualificação de leads — Porto Sul proteção veicular

**Data:** 2026-08-04
**Status:** especificação aprovada para planejamento (com decisões de roteiro em aberto, ver o fim do documento)

## Problema

A Porto Sul, empresa de proteção veicular, mantém listas de contatos (nome, telefone, veículo) e hoje aborda cada pessoa manualmente pelo WhatsApp. O processo não escala: a vendedora gasta o dia abrindo conversa e fazendo as mesmas perguntas de triagem, e só uma fração dos contatos tem interesse real.

O objetivo é automatizar a abertura da conversa e a qualificação, entregando à vendedora apenas os leads que valem atendimento humano.

## Escopo

**Dentro do escopo:**

- Importação de listas em CSV.
- Disparo da mensagem inicial pelo WhatsApp, com limite de 30 contatos por dia.
- Conversa de qualificação conduzida por IA, em tom natural e humano.
- Interpretação de áudio e imagem enviados pelo lead.
- Classificação do lead e transferência para a vendedora quando o lead está quente.
- Proteções contra bloqueio do número e conformidade com opt-out.
- Painel web de monitoramento, somente leitura.

**Fora do escopo:**

- Atendimento humano dentro do sistema. A vendedora responde pelo WhatsApp Web do próprio chip.
- Envio de propostas, cotações ou documentos.
- Integração com o CRM existente (projeto separado).
- Múltiplos atendentes ou múltiplos números.

## Decisões tomadas

| Decisão | Escolha | Motivo |
|---|---|---|
| Formato | Serviço único em Python | A qualificação é uma máquina de estados com histórico por lead; isso é mais curto, testável e depurável em código do que em fluxo visual. |
| WhatsApp | Evolution API (não oficial, self-hosted) no VPS existente | Já operada no VPS; sem custo por mensagem. Migração para a API oficial fica registrada como evolução futura. |
| Modelo de IA | `claude-haiku-4-5` | Conversa curta e roteiro definido não exigem mais capacidade. Custo estimado de US$ 18/mês para 900 leads. |
| Transcrição de áudio | `faster-whisper` local no VPS | O Claude não recebe áudio. Rodar local custa zero por mensagem e não adiciona fornecedor. |
| Banco | SQLite (arquivo único) | Volume de centenas de leads; backup é copiar um arquivo. |
| Número | Chip dedicado, já aquecido | Isola o risco de bloqueio do número principal da empresa. |
| Handoff | Aviso à vendedora; robô silencia o lead | Uma pessoa atende; painel de atendimento seria custo sem retorno. |
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
                      │                          [3b] Normalizador de mídia
                      │                            (áudio, imagem, outros)
                      │                                   ▼
                      └───── status ◄──── [4] Conversador (Haiku)
                                                          │
                                                 lead quente
                                                          ▼
                                             [5] Handoff à vendedora

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

**Mensagem inicial:** texto fixo com três variações, montado em código — sem chamada ao modelo. Apenas a saudação e o nome do lead. Sem links, sem imagens, sem menção à empresa.

**Após enviar:** lead vai para `contatado`, cota do dia incrementa, evento gravado.

### 3. Webhook receiver

**Faz:** recebe as mensagens que chegam da Evolution.

**Regras:**

- Grava a mensagem e devolve 200 imediatamente. Nenhum processamento pesado no handler.
- `wa_message_id` é UNIQUE — reenvio do mesmo webhook é ignorado silenciosamente.
- Mensagem de lead em estado terminal (`quente`, `frio`, `opt_out`, `sem_resposta`, `dado_desatualizado`) é apenas gravada; o robô não responde.
- Caso contrário, envia para o normalizador de mídia e depois para o conversador.

### 3b. Normalizador de mídia

**Faz:** converte qualquer tipo de mensagem recebida em algo que o modelo consiga ler.

| Tipo recebido | Tratamento |
|---|---|
| Texto | Passa direto |
| Áudio | Transcrito localmente por `faster-whisper` (modelo small, português). A transcrição entra no histórico e aparece no painel |
| Imagem | Enviada ao modelo como bloco de imagem — serve para foto do veículo ou de documento |
| Figurinha / GIF | Tratada como reação positiva; a conversa segue |
| Vídeo | Não processado. O robô responde pedindo que escreva |
| Localização / contato | Reconhecido e registrado; a conversa segue |
| Ligação perdida | Não retorna ligação. Responde propondo seguir por texto |

### 4. Conversador

**Faz:** conduz a conversa em tom natural e classifica o lead.

**Chamada:** uma por mensagem recebida, com o histórico da conversa e saída estruturada (`output_config.format`), para que a decisão venha tipada em vez de exigir interpretação de texto livre. O prompt do sistema é fixo e recebe `cache_control` para aproveitar o cache de prefixo.

**Retorno:**

| Campo | Descrição |
|---|---|
| `resposta` | Texto a enviar ao lead |
| `decisao` | `continuar`, `quente`, `frio`, `opt_out` ou `dado_desatualizado` |
| `resumo` | Uma linha para a vendedora |
| `paga_hoje` | Valor mensal atual, se informado |
| `tem_cobertura` | `sim`, `nao` ou `nao_informado` |
| `carro_quitado` | `quitado`, `financiado` ou `nao_informado` |

#### Roteiro

Cinco etapas. O robô só avança quando o lead responde; nunca envia dois blocos sem resposta no meio.

**0 · Abertura** — código, sem modelo. Três variações: `Oii {nome}, tudo bem?` / `Oi {nome}, tudo bem?` / `Bom dia {nome}, tudo bem?`

**1 · Confirmação do veículo**
> Tudo bem também. Vi aqui que você tem um Onix 2019, certo?

| Resposta do lead | Caminho |
|---|---|
| Confirma ("isso", "sim") | Segue para a etapa 2 |
| Confirma e pergunta o motivo ("isso, por que?") | **Pula a etapa 2** e vai direto à identificação. Ele pediu explicação; ignorar a pergunta soa evasivo |
| Nega ("não é meu", "vendi") | "Ah, entendi! Desculpa o incômodo então." → status `dado_desatualizado` |

**2 · Quebra-gelo**
> Você passa pretinho no pneu?

**3 · Identificação e benefício**
> Haha boa. Perguntei porque eu trabalho na Porto Sul, de proteção veicular, e a gente dá pretinho e cheirinho de graça de 6 em 6 meses pra quem é associado.

A abertura da frase acompanha o tom do lead: `Haha boa.` se ele foi descontraído, `Boa.` se foi seco, `Tranquilo.` se disse que não passa pretinho. O benefício é real e entregue pela Porto Sul.

**4 · Oferta e qualificação**
> Mas o principal não é o pretinho não, é a proteção em si — costuma sair bem abaixo de seguro. Hoje seu Onix tá protegido por alguma coisa?

Em seguida, conforme a resposta: quanto paga hoje, e se o carro é quitado ou financiado.

**5 · Fechamento**
> Quer que eu monte uma cotação pra você ver o valor? Sem compromisso nenhum.

Aceitou → `quente`, handoff.

#### Critério de lead quente

Qualquer um destes:

- Aceita receber a cotação.
- Paga por cobertura hoje e demonstra achar caro.
- Não tem cobertura e demonstra interesse.
- Pergunta preço, valor ou condições.
- Teve seguro recusado ou caro por perfil (motorista de aplicativo, carro antigo, idade).

**Teto:** doze mensagens do robô por lead. Atingido o teto sem decisão, o lead é marcado `quente` e passa à humana.

#### Regras de escrita

- Português neutro e educado, como uma pessoa escrevendo no WhatsApp.
- Voz feminina — concordância no feminino, "obrigada".
- Frases curtas, no máximo duas linhas. Resposta longa é quebrada em dois envios.
- Permitido: "tranquilo", "boa", "haha", "perfeito".
- Proibido: gíria pesada ("salve", "suave", "firmeza", "mano", "top") e o extremo formal ("prezado", "venho por meio desta").
- Sem emoji nas duas primeiras mensagens; depois, no máximo um, e só se o lead usar primeiro.
- Sem caixa alta, sem exclamação dupla.
- Espelha o registro do lead: seco com quem é seco, descontraído com quem brinca.

#### Proibições

- **Nunca inventar preço.** Quem calcula a cotação é a vendedora.
- **Nunca prometer cobertura** ou afirmar o que está incluso.
- **Nunca comentar o valor que o lead paga como "caro" ou "acima da média" sem que ele seja de fato elevado.** Se o valor for normal, responde neutro e segue.
- **Nunca negar que a abordagem é automatizada** se o lead perguntar diretamente se é robô, bot ou automático. Responde a verdade: a primeira abordagem é automática e a vendedora assume em seguida. Mentir nessa pergunta é o que gera denúncia, e denúncia é o que derruba o número.
- **Nunca insistir** após recusa.

### 5. Handoff

Quando o lead vira `quente`, o sistema avisa a vendedora com nome, telefone, veículo, resumo, a última fala do lead e o link `wa.me`. O robô silencia aquele número em definitivo.

### 6. Painel de monitoramento

Somente leitura, exceto por dois controles: kill switch e importação de CSV.

**Mostra:**

- Estado do sistema, chip conectado e relógio.
- Cota do dia com a posição na rampa.
- Contagem regressiva para o próximo disparo e a janela vigente.
- Disjuntor com os três limiares e seus valores atuais.
- Taxa de resposta dos últimos 14 dias.
- Tabela de leads com filtro por status e busca por nome ou telefone.
- Conversa completa de um lead, com transcrição dos áudios e a leitura do conversador.
- Feed de eventos em tempo real (SSE).

**Acesso:** o painel expõe dados pessoais de leads e o kill switch da operação. Fica atrás de autenticação (senha única do operador) e de HTTPS, nunca aberto na internet sem proteção.

Protótipo visual aprovado em 2026-08-04: https://claude.ai/code/artifact/2e704d95-cfff-465d-8102-d6f8d568b41a

## Proteções contra bloqueio do número

O objetivo é reduzir os sinais de comportamento que levam a bloqueio — denúncias, bloqueios de usuário, baixa taxa de resposta, volume anômalo. Nenhuma proteção depende de mascarar a automação perante a plataforma.

| # | Proteção | Regra |
|---|---|---|
| 1 | Rampa de volume | Dias 1–3: 10/dia. Dias 4–7: 20/dia. Dia 8 em diante: 30/dia. |
| 2 | Janela | 09:00–18:00, segunda a sexta. Configurável. |
| 3 | Ritmo | Intervalo sorteado de 2 a 8 minutos entre envios. |
| 4 | Verificação prévia | Número inexistente no WhatsApp é descartado sem consumir cota. |
| 5 | Abordagem de baixo atrito | Primeira mensagem com quatro palavras, sem link, sem imagem, sem menção comercial. Conversa natural sobe a taxa de resposta, e taxa de resposta alta é o sinal que mais protege o número. |
| 6 | Opt-out | Recusa em qualquer forma gera confirmação educada, status `opt_out` e blocklist permanente, consultada em toda importação futura. |
| 7 | Disjuntor | Pausa automática se, nos últimos 50 disparos: taxa de resposta abaixo de 10%, ou 3 ou mais opt-outs, ou qualquer falha de envio por bloqueio. Só volta com destravamento manual. |
| 8 | Sem insistência | Um disparo por lead. Sem resposta em 72h, o lead é encerrado como `sem_resposta`. Nenhum follow-up. |
| 9 | Kill switch | Pausa global imediata pelo painel. |

### Comportamento humano

Além de tornar a conversa crível, cada item abaixo aproxima o tráfego do número ao de uma pessoa real. Tudo isso é código, não decisão do modelo.

| Comportamento | Regra |
|---|---|
| Marcar como lida | 3 a 20 segundos depois de a mensagem chegar |
| "digitando…" | Presença `composing` por 2 a 8 s, proporcional ao tamanho da resposta |
| Tempo de resposta | 15 s a 3 min após a mensagem do lead, sorteado. Nunca instantâneo |
| Tamanho | Máximo duas linhas por mensagem; resposta longa vira dois envios com pausa de 8 a 20 s |
| Fora do horário | Mensagem recebida às 23h é lida, mas respondida só no próximo dia útil pela manhã |

**Fora do sistema, responsabilidade da empresa:** origem das listas e base legal sob a LGPD; entrega efetiva do pretinho e do cheirinho prometidos. O sistema preserva o histórico para atender pedidos de acesso ou exclusão, mas não decide sobre eles.

## Estados do lead

```
novo ──► contatado ──► em_conversa ──┬──► quente ──► (vendedora assume)
  │           │                      ├──► frio
  │           │                      ├──► opt_out
  │           │                      └──► dado_desatualizado
  │           └──► sem_resposta (72h sem retorno)
  └──► invalido (número não existe no WhatsApp)
```

O robô só fala com leads em `contatado` ou `em_conversa`.

## Modelo de dados

| Tabela | Campos principais |
|---|---|
| `leads` | id, nome, telefone_e164 (UNIQUE), veiculo, status, etapa, resumo, tem_cobertura, paga_hoje, carro_quitado, turnos, criado_em, contatado_em, ultimo_evento_em |
| `mensagens` | id, lead_id, direcao, tipo, texto, transcricao, wa_message_id (UNIQUE), criado_em |
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
| Resposta do modelo fora do schema, ou recusa | Lead passa à humana; o robô fica calado. |
| Transcrição de áudio falha | Robô pede que o lead escreva, sem travar a conversa. |
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
- Normalizador de mídia: cada tipo de mensagem produz a entrada esperada.

**Conversador:** testado com conversas gravadas como fixtures e o cliente do Claude mockado, verificando a decisão e a etapa para cada cenário — confirma o carro, nega o carro, pergunta o motivo, quente por preço, quente por recusa de seguradora, frio, opt-out, pergunta se é robô. Um teste opcional contra a API real, fora da suíte padrão.

## Custo operacional estimado

| Item | Mensal |
|---|---|
| API do Claude (900 leads, Haiku 4.5, com cache) | ~US$ 18 (~R$ 100) |
| Transcrição de áudio (local) | R$ 0 |
| VPS | já existente |
| Chip dedicado | R$ 0–50 |

## Decisões em aberto

A estrutura acima não depende delas — são ajustes de texto e de persona que podem ser feitos depois, sem retrabalho de arquitetura.

1. **Identidade do robô.** Ele escreve em primeira pessoa como a Rafaela (vendedora), e ela assume a mesma conversa sem que o lead perceba troca; ou ele se apresenta como assistente dela e faz a passagem explícita no fim. A recomendação é a primeira opção, pelo tom já definido.
2. **Redação final de cada etapa**, incluindo as variações de abertura e as frases de cada bifurcação.
3. **Nome usado na assinatura** caso o lead pergunte quem está falando.

## Evolução futura, fora deste escopo

- **API oficial do WhatsApp Business** (Meta Cloud API ou provedor). Elimina o risco de bloqueio por automação, ao custo de aprovação de template pela Meta e taxa por conversa (~R$ 300–500/mês neste volume). Exige base com opt-in. A arquitetura isola o envio em uma camada própria justamente para permitir essa troca sem reescrever o resto.
- Segmentação de listas por temperatura ou origem.
- Múltiplos atendentes com distribuição de leads.
- Resposta em áudio pelo robô.
