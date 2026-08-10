# src/disparo/powercrm.py
from __future__ import annotations

from dataclasses import dataclass

import httpx


class PowerCRMErro(RuntimeError):
    pass


class PowerCRMIndisponivel(PowerCRMErro):
    pass


class PowerCRMRecusa(PowerCRMErro):
    def __init__(self, status: int, detalhe: str) -> None:
        super().__init__(f"{status}: {detalhe}")
        self.status = status


@dataclass(frozen=True)
class Cotacao:
    cotacao_id: str
    negociacao_id: str
    plano: str
    mensalidade: str
    adesao: str


def _dinheiro(valor) -> str:
    return f"{float(valor):.2f}".replace(".", ",")


def _escolher_plano(planos: list[dict]) -> str:
    for plano in planos:
        if plano.get("isSelected"):
            return plano.get("name", "")
    for plano in planos:
        if plano.get("active"):
            return plano.get("name", "")
    return planos[0].get("name", "") if planos else ""


class PowerCRM:
    """Cliente da Power API (doc: https://power-crm.readme.io/reference)."""

    def __init__(self, base_url: str, token: str, http: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._cabecalhos = {"Authorization": f"Bearer {token}"}

    def _chamar(self, metodo: str, caminho: str, **kwargs) -> dict:
        try:
            resposta = self._http.request(
                metodo, f"{self._base}{caminho}",
                headers=self._cabecalhos, **kwargs,
            )
        except httpx.HTTPError as erro:
            raise PowerCRMIndisponivel(str(erro)) from erro
        if resposta.status_code >= 500:
            raise PowerCRMIndisponivel(f"HTTP {resposta.status_code}")
        if resposta.status_code >= 400:
            raise PowerCRMRecusa(resposta.status_code, resposta.text)
        return resposta.json()

    def cotar(self, nome: str, telefone: str, placa: str) -> Cotacao:
        dados = self._chamar("POST", "/api/quotation/add", json={
            "name": nome, "phone": telefone, "plts": placa,
        })
        # "sucess" e "negotationCode" são typos da própria API — não corrigir
        cotacao = dados.get("quotationResponse") or {}
        codigo = cotacao.get("quotationCode")
        if not dados.get("sucess") or not codigo:
            raise PowerCRMRecusa(200, f"cotacao recusada: {dados.get('errorVO')}")
        planos = self._chamar("GET", "/api/quotation/plansQuotation",
                              params={"quotationCode": codigo})
        return Cotacao(
            cotacao_id=str(codigo),
            negociacao_id=str(cotacao.get("negotationCode") or ""),
            plano=_escolher_plano(planos.get("plans") or []),
            mensalidade=_dinheiro(planos["monthlyPrice"]),
            adesao=_dinheiro(planos["acquisitionPrice"]),
        )
