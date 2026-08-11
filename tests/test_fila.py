from disparo.fila import FilaPorLead


def test_aguardar_passa_quando_nao_chega_mensagem_nova():
    fila = FilaPorLead()
    seq = fila.chegou(1)
    assert fila.aguardar(1, seq, 10.0, dormir=lambda s: None) is True


def test_aguardar_desiste_quando_chega_mensagem_mais_nova():
    fila = FilaPorLead()
    seq1 = fila.chegou(1)
    dormidas = []

    def dormir(s):
        dormidas.append(s)
        fila.chegou(1)  # mensagem nova chega durante a janela

    assert fila.aguardar(1, seq1, 10.0, dormir) is False
    assert dormidas == [10.0]


def test_rodada_mais_nova_assume_depois_da_desistencia():
    fila = FilaPorLead()
    fila.chegou(1)
    seq2 = fila.chegou(1)
    assert fila.aguardar(1, seq2, 10.0, dormir=lambda s: None) is True
    assert fila.assumir(1) is True


def test_assumir_durante_rodada_ativa_marca_pendencia():
    fila = FilaPorLead()
    fila.chegou(1)
    assert fila.assumir(1) is True
    assert fila.assumir(1) is False       # segunda rodada não entra
    assert fila.liberar(1) is True        # e deixou pendência
    assert fila.liberar(1) is False       # pendência consumida


def test_leads_diferentes_nao_interferem():
    fila = FilaPorLead()
    fila.chegou(1)
    assert fila.assumir(1) is True
    assert fila.assumir(2) is True


def test_mudou_detecta_chegada_posterior():
    fila = FilaPorLead()
    seq = fila.chegou(1)
    assert fila.mudou(1, seq) is False
    fila.chegou(1)
    assert fila.mudou(1, seq) is True
    assert fila.seq_atual(1) == seq + 1


def test_cobriu_registra_seq_respondido():
    fila = FilaPorLead()
    assert fila.seq_respondido(1) == 0    # nada respondido ainda
    fila.cobriu(1, 1)
    assert fila.seq_respondido(1) == 1
    fila.cobriu(1, 2)
    assert fila.seq_respondido(1) == 2


def test_cobriu_e_seq_atual_nao_interferem_entre_leads():
    fila = FilaPorLead()
    fila.chegou(1)
    fila.chegou(2)
    fila.cobriu(1, 1)
    assert fila.seq_respondido(1) == 1
    assert fila.seq_respondido(2) == 0


def test_tirar_midia_devolve_ultima_imagem_e_limpa():
    fila = FilaPorLead()
    fila.chegou(1, imagem_b64="abc", media_type="image/jpeg")
    fila.chegou(1, imagem_b64="def", media_type="image/png")
    assert fila.tirar_midia(1) == ("def", "image/png")
    assert fila.tirar_midia(1) is None
