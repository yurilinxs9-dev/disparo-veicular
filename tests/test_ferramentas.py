# tests/test_ferramentas.py
from datetime import datetime

from disparo.ferramentas import FERRAMENTAS_SPEC, Ferramentas
from disparo.maquina import Status, status_de, transicionar
from disparo.powercrm import Cotacao, PowerCRMIndisponivel, PowerCRMRecusa

AGORA = datetime(2026, 8, 7, 11, 0)


class PowerFalso:
    def __init__(self, fora_do_ar=False, recusa=False):
        self.fora_do_ar = fora_do_ar
        self.recusa = recusa

    def cotar(self, nome, telefone, placa):
        if self.fora_do_ar:
            raise PowerCRMIndisponivel("503")
        if self.recusa:
            raise PowerCRMRecusa(422, "placa invalida")
        return Cotacao("QTN-1", "NEG-1", "Master", "189,90", "250,00")


def _em_conversa(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)


def test_spec_tem_as_tres_ferramentas():
    assert {f["name"] for f in FERRAMENTAS_SPEC} == {
        "cotar", "gerar_cobranca", "escalar_humano"}


def test_cotar_grava_e_negocia(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    saida = f.executar("cotar", {"placa": "ABC1D23"})
    assert "189,90" in saida
    linha = conn.execute("SELECT * FROM leads WHERE id = ?", (lead,)).fetchone()
    assert linha["cotacao_id"] == "QTN-1"
    assert linha["negociacao_id"] == "NEG-1"
    assert linha["placa"] == "ABC1D23"
    assert status_de(conn, lead) == Status.NEGOCIANDO


def test_cotar_fora_do_ar_nao_explode(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(fora_do_ar=True), lead, AGORA)
    saida = f.executar("cotar", {"placa": "ABC1D23"})
    assert saida.startswith("erro:")
    assert f.falhas_powercrm == 1
    assert status_de(conn, lead) == Status.EM_CONVERSA


def test_cotar_recusa_nao_conta_como_falha(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(recusa=True), lead, AGORA)
    saida = f.executar("cotar", {"placa": "ABC1D23"})
    assert "recusada" in saida
    assert f.falhas_powercrm == 0
    assert status_de(conn, lead) == Status.EM_CONVERSA


def test_escalar(conn, lead):
    _em_conversa(conn, lead)
    f = Ferramentas(conn, PowerFalso(), lead, AGORA)
    f.executar("escalar_humano", {"motivo": "pediu desconto"})
    assert f.escalou is True
    assert status_de(conn, lead) == Status.ESCALADO
