# src/disparo/evolution.py
from __future__ import annotations

import httpx


class EvolutionIndisponivel(RuntimeError):
    """A Evolution API não respondeu, ou respondeu com erro de servidor."""


class Evolution:
    def __init__(self, base_url: str, api_key: str, instancia: str,
                 http: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._instancia = instancia
        self._http = http
        self._cabecalhos = {"apikey": api_key, "Content-Type": "application/json"}

    def _post(self, caminho: str, corpo: dict) -> httpx.Response:
        url = f"{self._base}/{caminho}/{self._instancia}"
        try:
            resposta = self._http.post(url, json=corpo, headers=self._cabecalhos)
        except httpx.HTTPError as erro:
            raise EvolutionIndisponivel(str(erro)) from erro
        if resposta.status_code >= 500:
            raise EvolutionIndisponivel(
                f"{resposta.status_code} em {caminho}: {resposta.text[:200]}"
            )
        resposta.raise_for_status()
        return resposta

    def numero_existe(self, telefone: str) -> bool:
        dados = self._post("chat/whatsappNumbers", {"numbers": [telefone]}).json()
        return bool(dados) and bool(dados[0].get("exists"))

    def enviar_texto(self, telefone: str, texto: str) -> str:
        dados = self._post(
            "message/sendText", {"number": telefone, "text": texto}
        ).json()
        return dados["key"]["id"]

    def marcar_lida(self, telefone: str, wa_message_id: str) -> None:
        self._post("chat/markMessageAsRead", {
            "readMessages": [
                {"remoteJid": f"{telefone}@s.whatsapp.net",
                 "id": wa_message_id, "fromMe": False}
            ]
        })

    def digitando(self, telefone: str, segundos: float) -> None:
        self._post("chat/sendPresence", {
            "number": telefone,
            "presence": "composing",
            "delay": int(segundos * 1000),
        })
