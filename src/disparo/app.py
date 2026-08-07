# src/disparo/app.py
from __future__ import annotations

import random
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from disparo import painel, webhook
from disparo.agendador import tentar_disparar
from disparo.config import carregar_config
from disparo.db import conectar, criar_schema
from disparo.evolution import Evolution
from disparo.midia import transcritor_whisper


def criar_app(estado) -> FastAPI:
    app = FastAPI(title="Disparo Porto Sul")
    app.state.estado = estado
    app.include_router(webhook.criar_rotas(estado))
    app.include_router(painel.criar_rotas(estado))

    @app.get("/saude")
    def saude() -> dict:
        return {"ok": True}

    return app


def montar_estado() -> SimpleNamespace:
    import anthropic

    cfg = carregar_config()
    conn = conectar(cfg.db)
    criar_schema(conn)
    return SimpleNamespace(
        cfg=cfg,
        conn=conn,
        evo=Evolution(cfg.evolution_base_url, cfg.evolution_api_key,
                      cfg.evolution_instance, httpx.Client(timeout=30)),
        claude=anthropic.Anthropic(api_key=cfg.anthropic_api_key),
        rng=random.SystemRandom(),
        transcritor=transcritor_whisper(cfg.whisper_modelo),
        dormir=time.sleep,
    )


def main() -> FastAPI:
    estado = montar_estado()
    app = criar_app(estado)

    agenda = BackgroundScheduler(timezone="America/Sao_Paulo")
    agenda.add_job(
        lambda: tentar_disparar(estado.conn, estado.evo, datetime.now(), estado.rng),
        "interval", minutes=1, id="disparo",
    )
    agenda.start()
    return app
