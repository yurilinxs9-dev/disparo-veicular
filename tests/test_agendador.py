# tests/test_agendador.py
import random
from datetime import date, datetime

from disparo.agendador import tentar_disparar
from disparo.cota import definir_inicio, enviados_no_dia
from disparo.disjuntor import pausar
from disparo.maquina import Status, status_de

RNG = random.Random(7)
TERCA_10H = datetime(2026, 8, 4, 10, 0)


class EvoFalsa:
    def __init__(self, existe=True, falha=False):
        self.existe = existe
        self.falha = falha
        self.enviados: list[tuple[str, str]] = []

    def numero_existe(self, telefone):
        return self.existe

    def enviar_texto(self, telefone, texto):
        if self.falha:
            from disparo.evolution import EvolutionIndisponivel
            raise EvolutionIndisponivel("caiu")
        self.enviados.append((telefone, texto))
        return f"WA{len(self.enviados)}"


def test_dispara_lead_novo(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    evo = EvoFalsa()
    r = tentar_disparar(conn, evo, TERCA_10H, RNG)
    assert r.enviou is True
    assert len(evo.enviados) == 1
    assert "Joao" in evo.enviados[0][1]
    assert status_de(conn, lead) == Status.CONTATADO
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 1


def test_nao_dispara_fora_da_janela(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    evo = EvoFalsa()
    r = tentar_disparar(conn, evo, datetime(2026, 8, 4, 20, 0), RNG)
    assert r.enviou is False
    assert r.motivo == "fora da janela"
    assert evo.enviados == []


def test_nao_dispara_pausado(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    pausar(conn, "teste", TERCA_10H)
    r = tentar_disparar(conn, EvoFalsa(), TERCA_10H, RNG)
    assert r.enviou is False
    assert r.motivo == "pausado"


def test_numero_inexistente_nao_gasta_cota(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    evo = EvoFalsa(existe=False)
    r = tentar_disparar(conn, evo, TERCA_10H, RNG)
    assert r.enviou is False
    assert status_de(conn, lead) == Status.INVALIDO
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 0


def test_falha_de_envio_nao_gasta_cota_e_pausa(conn, lead):
    definir_inicio(conn, date(2026, 8, 3))
    r = tentar_disparar(conn, EvoFalsa(falha=True), TERCA_10H, RNG)
    assert r.enviou is False
    assert enviados_no_dia(conn, date(2026, 8, 4)) == 0
    assert status_de(conn, lead) == Status.NOVO


def test_sem_lead_novo(conn):
    definir_inicio(conn, date(2026, 8, 3))
    r = tentar_disparar(conn, EvoFalsa(), TERCA_10H, RNG)
    assert r.enviou is False
    assert r.motivo == "sem lead novo"
