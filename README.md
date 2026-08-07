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

## Rodar os testes

```bash
pip install -e ".[dev]"
pytest -v
```

## Parar tudo agora

Botão **Pausar tudo** no painel. Nenhum disparo sai enquanto estiver pausado.
