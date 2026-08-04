from datetime import datetime

import pytest

from disparo.maquina import (Status, TransicaoInvalida, robo_pode_falar,
                             status_de, transicionar)

AGORA = datetime(2026, 8, 4, 10, 0)


def test_caminho_feliz(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.QUENTE, AGORA)
    assert status_de(conn, lead) == Status.QUENTE


def test_nao_pode_pular_de_novo_para_quente(conn, lead):
    with pytest.raises(TransicaoInvalida):
        transicionar(conn, lead, Status.QUENTE, AGORA)


def test_estado_terminal_nao_muda_mais(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.OPT_OUT, AGORA)
    with pytest.raises(TransicaoInvalida):
        transicionar(conn, lead, Status.EM_CONVERSA, AGORA)


def test_robo_so_fala_em_dois_estados():
    assert robo_pode_falar(Status.CONTATADO) is True
    assert robo_pode_falar(Status.EM_CONVERSA) is True
    for s in (Status.NOVO, Status.QUENTE, Status.FRIO, Status.OPT_OUT,
              Status.SEM_RESPOSTA, Status.DADO_DESATUALIZADO, Status.INVALIDO):
        assert robo_pode_falar(s) is False
