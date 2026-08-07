# src/disparo/webhook.py
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Request

from disparo.midia import normalizar
from disparo.resposta import processar


def criar_rotas(estado) -> APIRouter:
    rotas = APIRouter()

    @rotas.post("/webhook")
    async def receber(request: Request, tarefas: BackgroundTasks) -> dict:
        payload = await request.json()
        mensagem = normalizar(payload, estado.transcritor)
        if mensagem is None or not mensagem.telefone:
            return {"ok": True}
        tarefas.add_task(
            processar, estado.conn, estado.evo, estado.claude, estado.cfg,
            mensagem, datetime.now(), estado.rng, estado.dormir,
            getattr(estado, "powercrm", None),
        )
        return {"ok": True}

    return rotas
