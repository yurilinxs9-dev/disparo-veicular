# tests/test_webhook.py
import random
from types import SimpleNamespace

from fastapi.testclient import TestClient

from disparo.app import criar_app


class EvoFalsa:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return "WA-out"

    def marcar_lida(self, telefone, wa_message_id):
        pass

    def digitando(self, telefone, segundos):
        pass


def _estado(conn):
    from disparo.conversador import Qualificacao
    q = Qualificacao(resposta="Tudo bem também.", decisao="continuar", resumo="",
                     paga_hoje=None, tem_cobertura="nao_informado",
                     carro_quitado="nao_informado")
    return SimpleNamespace(
        conn=conn,
        evo=EvoFalsa(),
        claude=SimpleNamespace(messages=SimpleNamespace(
            parse=lambda **kw: SimpleNamespace(parsed_output=q))),
        cfg=SimpleNamespace(vendedora_telefone="5511900000000",
                            painel_senha="segredo"),
        rng=random.Random(1),
        transcritor=lambda b: "",
        dormir=lambda s: None,
    )


def test_webhook_aceita_e_responde_200(conn, lead):
    conn.execute("UPDATE leads SET status = 'contatado' WHERE id = ?", (lead,))
    conn.commit()
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    corpo = {"data": {"key": {"id": "WA-in", "remoteJid": "5511988884444@s.whatsapp.net",
                              "fromMe": False},
                      "message": {"conversation": "tudo bem e vc"}}}
    resposta = cliente.post("/webhook", json=corpo)
    assert resposta.status_code == 200
    assert estado.evo.enviados


def test_webhook_ignora_mensagem_propria(conn, lead):
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    corpo = {"data": {"key": {"id": "WA-x", "remoteJid": "5511988884444@s.whatsapp.net",
                              "fromMe": True},
                      "message": {"conversation": "oi"}}}
    assert cliente.post("/webhook", json=corpo).status_code == 200
    assert estado.evo.enviados == []


def test_webhook_com_corpo_estranho_nao_quebra(conn):
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    assert cliente.post("/webhook", json={"foo": "bar"}).status_code == 200
