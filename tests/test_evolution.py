# tests/test_evolution.py
import json

import httpx
import pytest
import respx

from disparo.evolution import (
    Evolution,
    EvolutionErro,
    EvolutionIndisponivel,
    EvolutionRecusou,
)

BASE = "http://evo:8080"


def _cliente() -> Evolution:
    return Evolution(BASE, "chave", "portosul-01", httpx.Client(timeout=5))


@respx.mock
def test_numero_existe():
    rota = respx.post(f"{BASE}/chat/whatsappNumbers/portosul-01").mock(
        return_value=httpx.Response(200, json=[{"exists": True, "jid": "x"}])
    )
    assert _cliente().numero_existe("5511988884444") is True
    assert json.loads(rota.calls.last.request.content) == {
        "numbers": ["5511988884444"]
    }


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
    assert json.loads(rota.calls.last.request.content) == {
        "number": "5511988884444",
        "text": "oi",
    }


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


@respx.mock
def test_marcar_lida_envia_read_messages():
    rota = respx.post(f"{BASE}/chat/markMessageAsRead/portosul-01").mock(
        return_value=httpx.Response(200, json={})
    )
    _cliente().marcar_lida("5511988884444", "WA123")
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {
        "readMessages": [
            {
                "remoteJid": "5511988884444@s.whatsapp.net",
                "id": "WA123",
                "fromMe": False,
            }
        ]
    }


@respx.mock
def test_digitando_envia_presence_e_delay_em_milissegundos():
    rota = respx.post(f"{BASE}/chat/sendPresence/portosul-01").mock(
        return_value=httpx.Response(200, json={})
    )
    _cliente().digitando("5511988884444", 3.5)
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {
        "number": "5511988884444",
        "presence": "composing",
        "delay": 3500,
    }


@respx.mock
def test_status_401_vira_evolution_recusou():
    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    with pytest.raises(EvolutionRecusou) as excinfo:
        _cliente().enviar_texto("5511988884444", "oi")
    assert excinfo.value.status == 401


@respx.mock
def test_status_404_vira_evolution_recusou():
    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(EvolutionRecusou):
        _cliente().enviar_texto("5511988884444", "oi")


@respx.mock
def test_evolution_recusou_e_indisponivel_sao_evolution_erro():
    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    with pytest.raises(EvolutionErro):
        _cliente().enviar_texto("5511988884444", "oi")

    respx.post(f"{BASE}/message/sendText/portosul-01").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )
    with pytest.raises(EvolutionErro):
        _cliente().enviar_texto("5511988884444", "oi")
