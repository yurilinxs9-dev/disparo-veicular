# tests/test_evolution.py
import httpx
import pytest
import respx

from disparo.evolution import Evolution, EvolutionIndisponivel

BASE = "http://evo:8080"


def _cliente() -> Evolution:
    return Evolution(BASE, "chave", "portosul-01", httpx.Client(timeout=5))


@respx.mock
def test_numero_existe():
    respx.post(f"{BASE}/chat/whatsappNumbers/portosul-01").mock(
        return_value=httpx.Response(200, json=[{"exists": True, "jid": "x"}])
    )
    assert _cliente().numero_existe("5511988884444") is True


@respx.mock
def test_numero_nao_existe():
    respx.post(f"{BASE}/chat/whatsappNumbers/portosul-01").mock(
        return_value=httpx.Response(200, json=[{"exists": False}])
    )
    assert _cliente().numero_existe("5511988884444") is False


@respx.mock
def test_enviar_texto_devolve_id():
    rota = respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(201, json={"key": {"id": "WA123"}})
    )
    assert _cliente().enviar_texto("5511988884444", "oi") == "WA123"
    assert rota.calls.last.request.headers["apikey"] == "chave"


@respx.mock
def test_erro_de_rede_vira_excecao_do_dominio():
    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        side_effect=httpx.ConnectError("sem rede")
    )
    with pytest.raises(EvolutionIndisponivel):
        _cliente().enviar_texto("5511988884444", "oi")


@respx.mock
def test_status_5xx_vira_excecao_do_dominio():
    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )
    with pytest.raises(EvolutionIndisponivel):
        _cliente().enviar_texto("5511988884444", "oi")
