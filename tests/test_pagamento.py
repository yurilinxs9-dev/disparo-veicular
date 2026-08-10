# tests/test_pagamento.py
import random
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from disparo.app import criar_app
from disparo.maquina import Status, status_de, transicionar

AGORA = datetime(2026, 8, 7, 12, 0)
CABECALHO = {"Authorization": "Bearer whk"}


class EvoFalsa:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return "WA-out"

    def marcar_lida(self, *a):
        pass

    def digitando(self, *a):
        pass


def _estado(conn):
    return SimpleNamespace(
        conn=conn, evo=EvoFalsa(), claude=None, rng=random.Random(1),
        transcritor=lambda b: "", dormir=lambda s: None, powercrm=None,
        cfg=SimpleNamespace(vendedora_telefone="5511900000000",
                            equipe_telefone="5537999990000",
                            painel_senha="segredo",
                            powercrm_webhook_token="whk"),
    )


def _aguardando(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.NEGOCIANDO, AGORA)
    transicionar(conn, lead, Status.AGUARDANDO_PAGAMENTO, AGORA)
    conn.execute("UPDATE leads SET cotacao_id = 'QTN-1' WHERE id = ?", (lead,))
    conn.commit()


def _evento(tipo, cotacao="QTN-1"):
    return {
        "id": "evt_1", "type": tipo, "version": "2026-04-01",
        "occurredAt": "2026-08-07T15:00:00Z", "companyId": 1,
        "subject": {"type": "quotation", "id": "98765", "code": cotacao},
        "data": {"legacyType": "PAYMENT", "status": "x",
                 "negotiationCode": "NEG-1", "quotationCode": cotacao,
                 "hash": ""},
        "metadata": {"correlationId": "c1", "source": "powercrm"},
    }


def test_sem_token_e_401(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm", json=_evento("payment.slip.paid"))
    assert r.status_code == 401


def test_boleto_pago_confirma_avisa_cliente_e_equipe(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.paid"))
    assert r.status_code == 200
    assert status_de(conn, lead) == Status.PAGO
    destinos = [d for d, _ in estado.evo.enviados]
    assert "5511988884444" in destinos      # boas-vindas ao cliente
    assert "5537999990000" in destinos      # vistoria pra equipe


def test_pix_pago_tambem_confirma(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    cliente.post("/webhook/powercrm", headers=CABECALHO,
                 json=_evento("payment.pix.paid"))
    assert status_de(conn, lead) == Status.PAGO


def test_evento_repetido_e_ignorado(conn, lead):
    _aguardando(conn, lead)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    cliente.post("/webhook/powercrm", headers=CABECALHO,
                 json=_evento("payment.slip.paid"))
    cliente.post("/webhook/powercrm", headers=CABECALHO,
                 json=_evento("payment.slip.paid"))
    assert len(estado.evo.enviados) == 2  # só o primeiro teve efeito


def test_boleto_gerado_rearma_o_lembrete(conn, lead):
    _aguardando(conn, lead)
    conn.execute("UPDATE leads SET cobranca_enviada_em = '2026-08-01T00:00:00', "
                 "lembrete_em = '2026-08-03T00:00:00' WHERE id = ?", (lead,))
    conn.commit()
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.generated"))
    assert r.status_code == 200
    linha = conn.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
    assert linha["cobranca_enviada_em"] != "2026-08-01T00:00:00"
    assert linha["lembrete_em"] is None
    assert status_de(conn, lead) == Status.AGUARDANDO_PAGAMENTO


def test_pagamento_apos_escalada_avisa_equipe_sem_transicionar(conn, lead):
    _aguardando(conn, lead)
    transicionar(conn, lead, Status.ESCALADO, AGORA)
    estado = _estado(conn)
    cliente = TestClient(criar_app(estado))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.paid"))
    assert r.status_code == 200
    assert status_de(conn, lead) == Status.ESCALADO  # nenhuma transição
    destinos = [d for d, _ in estado.evo.enviados]
    assert "5537999990000" in destinos  # equipe alertada


def test_cotacao_desconhecida_nao_quebra(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("payment.slip.paid", cotacao="ZZZ"))
    assert r.status_code == 200


def test_evento_irrelevante_devolve_200(conn, lead):
    _aguardando(conn, lead)
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm", headers=CABECALHO,
                     json=_evento("negotiation.stage.changed"))
    assert r.status_code == 200


def test_corpo_invalido_devolve_200(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    r = cliente.post("/webhook/powercrm",
                     headers={**CABECALHO, "Content-Type": "application/json"},
                     content=b"nao e json")
    assert r.status_code == 200
