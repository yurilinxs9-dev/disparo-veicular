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

## Venda autônoma (Etapa 2)

Com `POWERCRM_BASE_URL` e `POWERCRM_TOKEN` no `.env`, a IA cota pela placa
(`POST /api/quotation/add` seguido de `GET /api/quotation/plansQuotation`),
apresenta o preço de tabela e, no aceite explícito do cliente, aciona a
ferramenta `fechar_venda`: o lead vai para `aguardando_pagamento` e a equipe é
avisada ("VENDA FECHADA") para gerar o boleto manualmente no Power CRM e
mandá-lo na própria conversa — a Power API não tem endpoint de cobrança. Sem
`POWERCRM_BASE_URL`/`POWERCRM_TOKEN` o serviço opera no modo da Etapa 1
(qualifica e escala para humano).

A confirmação de pagamento chega pelo **Webhook V2** do Power CRM em
`/webhook/powercrm`: envelope `{id, type, version, occurredAt, companyId,
subject, data: {quotationCode, negotiationCode, ...}, metadata}`. Tipos pagos:
`payment.slip.paid`, `payment.pix.paid`, `payment.card.paid`,
`payment.cash.paid`; `payment.slip.generated` apenas rearma o lembrete de 48h.
O lead é casado pelo `data.quotationCode`. **O Power CRM desativa o webhook se
o endpoint responder qualquer erro** — por isso ele sempre devolve 200 depois
de validar o token.

Configurar no Power CRM (Minha Empresa → Plugins):
1. Power API → Adicionar → Gerar Token, marcar "acesso a informações de
   contato do lead", Salvar. Colocar o token em `POWERCRM_TOKEN`.
2. Power Webhook → Adicionar: URL `https://seu-dominio/webhook/powercrm`,
   Token = `POWERCRM_WEBHOOK_TOKEN` (segredo nosso), Tipo "Status do
   pagamento", Status Ativo.

Regras fixas: preço de tabela sem desconto; cobrança só após aceite explícito;
lembrete único de boleto em 48h (funciona com ou sem link de boleto salvo,
já que a equipe pode mandar o boleto direto na conversa); sem pagamento em
72h a equipe assume.

## Rodar os testes

```bash
pip install -e ".[dev]"
pytest -v
```

## Parar tudo agora

Botão **Pausar tudo** no painel. Nenhum disparo sai enquanto estiver pausado.
