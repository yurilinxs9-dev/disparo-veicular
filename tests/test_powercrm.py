import httpx
import pytest
import respx

from disparo.powercrm import (Cobranca, Cotacao, PowerCRM, PowerCRMIndisponivel,
                              PowerCRMRecusa)

BASE = "https://api.powercrm.test"


def _cliente():
    return PowerCRM(BASE, "tok", httpx.Client())


@respx.mock
def test_cotar_devolve_cotacao():
    rota = respx.post(f"{BASE}/cotacoes").respond(200, json={
        "id": "C1", "plano": "Master", "mensalidade": "189.90", "adesao": "250.00",
    })
    c = _cliente().cotar("Joao", "5537988884444", "ABC1D23")
    assert c == Cotacao("C1", "Master", "189.90", "250.00")
    corpo = rota.calls.last.request
    assert corpo.headers["Authorization"] == "Bearer tok"


@respx.mock
def test_gerar_cobranca():
    respx.post(f"{BASE}/cotacoes/C1/cobrancas").respond(200, json={
        "id": "B1", "url_boleto": "https://p/b1", "pix_copia_cola": "000201...",
    })
    b = _cliente().gerar_cobranca("C1")
    assert b == Cobranca("B1", "https://p/b1", "000201...")


@respx.mock
def test_4xx_vira_recusa():
    respx.post(f"{BASE}/cotacoes").respond(422, json={"detail": "placa invalida"})
    with pytest.raises(PowerCRMRecusa) as e:
        _cliente().cotar("Joao", "5537988884444", "XXX")
    assert e.value.status == 422


@respx.mock
def test_5xx_vira_indisponivel():
    respx.post(f"{BASE}/cotacoes").respond(503)
    with pytest.raises(PowerCRMIndisponivel):
        _cliente().cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_timeout_vira_indisponivel():
    respx.post(f"{BASE}/cotacoes").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(PowerCRMIndisponivel):
        _cliente().cotar("Joao", "5537988884444", "ABC1D23")
