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
    plano: str
    mensalidade: str
    adesao: str


@dataclass(frozen=True)
class Cobranca:
    cobranca_id: str
    url_boleto: str
    pix_copia_cola: str


class PowerCRM:
    """Contrato assumido da Power API — ajustar aqui quando a doc real chegar."""

    def __init__(self, base_url: str, token: str, http: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._cabecalhos = {"Authorization": f"Bearer {token}"}

    def _post(self, caminho: str, corpo: dict) -> dict:
        try:
            resposta = self._http.post(
                f"{self._base}{caminho}", json=corpo, headers=self._cabecalhos,
            )
        except httpx.HTTPError as erro:
            raise PowerCRMIndisponivel(str(erro)) from erro
        if resposta.status_code >= 500:
            raise PowerCRMIndisponivel(f"HTTP {resposta.status_code}")
        if resposta.status_code >= 400:
            raise PowerCRMRecusa(resposta.status_code, resposta.text)
        return resposta.json()

    def cotar(self, nome: str, telefone: str, placa: str) -> Cotacao:
        dados = self._post("/cotacoes", {
            "nome": nome, "telefone": telefone, "placa": placa,
        })
        return Cotacao(str(dados["id"]), dados["plano"],
                       str(dados["mensalidade"]), str(dados["adesao"]))

    def gerar_cobranca(self, cotacao_id: str) -> Cobranca:
        dados = self._post(f"/cotacoes/{cotacao_id}/cobrancas", {})
        return Cobranca(str(dados["id"]), dados["url_boleto"],
                        dados["pix_copia_cola"])
