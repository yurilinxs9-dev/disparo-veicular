import httpx
import pytest
import respx

from disparo.powercrm import (Cotacao, PowerCRM, PowerCRMIndisponivel,
                              PowerCRMRecusa)

BASE = "https://api.powercrm.test"

ADD_OK = {
    "sucess": True,
    "quotationResponse": {"quotationCode": "QTN-1", "negotationCode": "NEG-1"},
    "errorVO": None,
}
PLANOS_OK = {
    "quotationCode": "QTN-1", "acquisitionPrice": 250.0, "monthlyPrice": 189.9,
    "plans": [
        {"planId": 5, "name": "Basico", "isSelected": False, "active": True},
        {"planId": 7, "name": "Master", "isSelected": True, "active": True},
    ],
}


@pytest.fixture
def cliente():
    with httpx.Client() as http:
        yield PowerCRM(BASE, "tok", http)


@respx.mock
def test_cotar_faz_as_duas_chamadas(cliente):
    rota_add = respx.post(f"{BASE}/api/quotation/add").respond(201, json=ADD_OK)
    rota_planos = respx.get(f"{BASE}/api/quotation/plansQuotation").respond(
        200, json=PLANOS_OK)
    c = cliente.cotar("Joao", "5537988884444", "ABC1D23")
    assert c == Cotacao("QTN-1", "NEG-1", "Master", "189,90", "250,00")
    corpo = rota_add.calls.last.request
    assert corpo.headers["Authorization"] == "Bearer tok"
    import json
    assert json.loads(corpo.content) == {
        "name": "Joao", "phone": "5537988884444", "plts": "ABC1D23"}
    assert rota_planos.calls.last.request.url.params["quotationCode"] == "QTN-1"


@respx.mock
def test_cotar_sem_plano_selecionado_usa_o_primeiro_ativo(cliente):
    planos = dict(PLANOS_OK)
    planos["plans"] = [
        {"planId": 1, "name": "Inativo", "isSelected": False, "active": False},
        {"planId": 5, "name": "Basico", "isSelected": False, "active": True},
    ]
    respx.post(f"{BASE}/api/quotation/add").respond(201, json=ADD_OK)
    respx.get(f"{BASE}/api/quotation/plansQuotation").respond(200, json=planos)
    assert cliente.cotar("Joao", "5537988884444", "ABC1D23").plano == "Basico"


@respx.mock
def test_add_sem_sucesso_vira_recusa(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(
        201, json={"sucess": False, "quotationResponse": None,
                   "errorVO": {"msg": "sem tabela de preco"}})
    with pytest.raises(PowerCRMRecusa):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_412_vira_recusa(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(
        412, json={"errors": ["placa invalida"]})
    with pytest.raises(PowerCRMRecusa) as e:
        cliente.cotar("Joao", "5537988884444", "XXX")
    assert e.value.status == 412


@respx.mock
def test_5xx_vira_indisponivel(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(503)
    with pytest.raises(PowerCRMIndisponivel):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_5xx_na_busca_de_planos_vira_indisponivel(cliente):
    respx.post(f"{BASE}/api/quotation/add").respond(201, json=ADD_OK)
    respx.get(f"{BASE}/api/quotation/plansQuotation").respond(500)
    with pytest.raises(PowerCRMIndisponivel):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")


@respx.mock
def test_timeout_vira_indisponivel(cliente):
    respx.post(f"{BASE}/api/quotation/add").mock(
        side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(PowerCRMIndisponivel):
        cliente.cotar("Joao", "5537988884444", "ABC1D23")
