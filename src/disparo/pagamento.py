# src/disparo/pagamento.py
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status

from disparo import eventos, handoff
from disparo.humano import primeiro_nome
from disparo.maquina import Status, status_de, transicionar

BOAS_VINDAS = ("Pagamento confirmado, {nome}! Seja bem-vindo à Porto Sul. "
               "A equipe já vai te chamar pra agendar a vistoria do veículo.")


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
        if not isinstance(corpo, dict):
            return {"ok": True}
        tipo = str(corpo.get("type", ""))
        dados = corpo.get("data")
        if not isinstance(dados, dict):
            dados = {}
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
