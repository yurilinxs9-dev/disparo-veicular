# src/disparo/pagamento.py
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status

from disparo import handoff
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
