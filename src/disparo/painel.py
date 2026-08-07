# src/disparo/painel.py
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     UploadFile, status)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from disparo import cota, disjuntor, eventos
from disparo.importador import importar_csv

ESTATICO = Path(__file__).parent / "static"
_seguranca = HTTPBasic()


def criar_rotas(estado) -> APIRouter:
    rotas = APIRouter()

    def autenticar(credenciais: HTTPBasicCredentials = Depends(_seguranca)) -> None:
        if not secrets.compare_digest(credenciais.password, estado.cfg.painel_senha):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "senha incorreta",
                {"WWW-Authenticate": "Basic"},
            )

    @rotas.get("/painel")
    def pagina() -> FileResponse:
        return FileResponse(ESTATICO / "painel.html")

    @rotas.get("/api/estado", dependencies=[Depends(autenticar)])
    def ler_estado() -> dict:
        hoje = datetime.now().date()
        veredito = disjuntor.avaliar(estado.conn)
        return {
            "pausado": disjuntor.esta_pausado(estado.conn),
            "limite": cota.limite_do_dia(estado.conn, hoje),
            "enviados": cota.enviados_no_dia(estado.conn, hoje),
            "disjuntor": {"ok": veredito.ok, "motivo": veredito.motivo},
        }

    @rotas.get("/api/leads", dependencies=[Depends(autenticar)])
    def listar_leads(status_filtro: str | None = Query(None, alias="status"),
                     busca: str | None = None) -> list[dict]:
        sql = "SELECT * FROM leads WHERE 1=1"
        params: list = []
        if status_filtro:
            sql += " AND status = ?"
            params.append(status_filtro)
        if busca:
            sql += " AND (nome LIKE ? OR telefone_e164 LIKE ?)"
            params += [f"%{busca}%", f"%{busca}%"]
        sql += " ORDER BY COALESCE(ultimo_evento_em, criado_em) DESC LIMIT 200"
        return [dict(linha) for linha in estado.conn.execute(sql, params)]

    @rotas.get("/api/leads/{lead_id}", dependencies=[Depends(autenticar)])
    def ler_lead(lead_id: int) -> dict:
        lead = estado.conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if lead is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "lead não encontrado")
        mensagens = estado.conn.execute(
            "SELECT direcao, tipo, texto, criado_em FROM mensagens "
            "WHERE lead_id = ? ORDER BY id",
            (lead_id,),
        ).fetchall()
        return {"lead": dict(lead), "mensagens": [dict(m) for m in mensagens]}

    @rotas.post("/api/pausar", dependencies=[Depends(autenticar)])
    async def alternar_pausa(request: Request) -> dict:
        corpo = await request.json()
        agora = datetime.now()
        if corpo.get("pausar"):
            disjuntor.pausar(estado.conn, "pausa manual pelo painel", agora)
        else:
            disjuntor.retomar(estado.conn, agora)
        return {"pausado": disjuntor.esta_pausado(estado.conn)}

    @rotas.post("/api/importar", dependencies=[Depends(autenticar)])
    async def importar(arquivo: UploadFile = File(...)) -> dict:
        conteudo = (await arquivo.read()).decode("utf-8-sig")
        rel = importar_csv(estado.conn, conteudo, datetime.now())
        eventos.registrar(
            estado.conn, "sistema",
            f"Importação: {rel.importados} novos de {rel.lidos} lidos",
            datetime.now(),
        )
        return rel.__dict__

    @rotas.get("/api/eventos/stream", dependencies=[Depends(autenticar)])
    async def stream() -> StreamingResponse:
        async def gerar():
            ultimo = 0
            while True:
                novos = [
                    e for e in reversed(eventos.listar(estado.conn, 50))
                    if e["id"] > ultimo
                ]
                for evento in novos:
                    ultimo = evento["id"]
                    yield f"data: {json.dumps(evento)}\n\n"
                await asyncio.sleep(3)

        return StreamingResponse(gerar(), media_type="text/event-stream")

    return rotas
