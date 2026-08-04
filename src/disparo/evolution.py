# src/disparo/evolution.py
from __future__ import annotations

import httpx


class EvolutionErro(RuntimeError):
    """Falha ao falar com a Evolution API."""


class EvolutionIndisponivel(EvolutionErro):
    """Transitorio: erro de transporte ou 5xx. Vale tentar de novo."""


class EvolutionRecusou(EvolutionErro):
    """Permanente: 4xx. Chave, instancia ou requisicao errada — precisa de gente."""

    def __init__(self, status: int, detalhe: str) -> None:
        super().__init__(f"{status}: {detalhe}")
        self.status = status


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
        if resposta.status_code >= 400:
            raise EvolutionRecusou(resposta.status_code, resposta.text[:200])
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
