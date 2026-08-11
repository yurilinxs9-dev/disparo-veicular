# tests/test_humano.py
import random

from disparo.humano import (atraso_leitura, atraso_resposta,
                            duracao_digitando, intervalo_entre_disparos,
                            janela_debounce, primeiro_nome, quebrar)

RNG = random.Random(42)


def test_primeiro_nome_extrai_o_primeiro_pedaco():
    assert primeiro_nome("Joao da Silva") == "Joao"


def test_primeiro_nome_vazio_cai_para_cliente():
    assert primeiro_nome("") == "cliente"
    assert primeiro_nome("   ") == "cliente"


def test_janela_debounce_entre_8_e_20_segundos():
    rng = random.Random(1)
    for _ in range(200):
        assert 8 <= janela_debounce(rng) <= 20


def test_atraso_leitura_entre_2_e_8_segundos():
    rng = random.Random(1)
    for _ in range(200):
        assert 2 <= atraso_leitura(rng) <= 8


def test_atraso_resposta_entre_3_e_25_segundos():
    rng = random.Random(1)
    for _ in range(200):
        assert 3 <= atraso_resposta(rng) <= 25


def test_intervalo_entre_disparos_entre_120_e_480_segundos():
    rng = random.Random(1)
    for _ in range(200):
        assert 120 <= intervalo_entre_disparos(rng) <= 480


def test_digitando_cresce_com_o_texto():
    curto = duracao_digitando("oi", RNG)
    longo = duracao_digitando("x" * 300, RNG)
    assert longo > curto
    assert 2 <= curto <= 8 and 2 <= longo <= 8


def test_texto_curto_nao_quebra():
    assert quebrar("Tudo bem também.") == ["Tudo bem também."]


def test_texto_longo_quebra_em_frases():
    limite = 120
    texto = ("Perguntei porque eu trabalho na Porto Sul, de proteção veicular. "
             "A gente dá pretinho e cheirinho de graça de 6 em 6 meses pra quem "
             "é associado. Mas o principal é a proteção em si.")
    partes = quebrar(texto, limite=limite)
    assert len(partes) >= 2
    assert all(len(p) <= limite for p in partes)
    assert "".join(partes).replace(" ", "") == texto.replace(" ", "")


def test_texto_sem_pontuacao_quebra_em_pedacos_dentro_do_limite():
    limite = 160
    texto = "x" * 300
    partes = quebrar(texto, limite=limite)
    assert len(partes) > 1
    assert all(len(p) <= limite for p in partes)


def test_frase_longa_sem_pontuacao_de_fim_respeita_o_limite():
    limite = 50
    texto = " ".join(["palavra"] * 30)  # sem . ! ? em lugar nenhum
    partes = quebrar(texto, limite=limite)
    assert len(partes) > 1
    assert all(len(p) <= limite for p in partes)


def test_frase_longa_sem_pontuacao_reconstroi_o_texto_original():
    limite = 50
    texto = " ".join(["palavra"] * 30)
    partes = quebrar(texto, limite=limite)
    assert "".join(partes).replace(" ", "") == texto.replace(" ", "")


def test_palavra_isolada_maior_que_o_limite_fica_sozinha_e_nada_se_perde():
    limite = 20
    texto = "oi " + ("abcdefghijklmnopqrstuvwxyz" * 3) + " tudo bem"
    partes = quebrar(texto, limite=limite)
    # a palavra gigante não gruda no "oi" nem no "tudo bem" vizinhos
    assert "oi" in partes
    assert "tudo bem" in partes
    assert "".join(partes).replace(" ", "") == texto.replace(" ", "")
