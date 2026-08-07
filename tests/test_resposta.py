import random
from datetime import datetime
from types import SimpleNamespace

from disparo.blocklist import esta_bloqueado
from disparo.conversador import Qualificacao
from disparo.maquina import Status, status_de, transicionar
from disparo.midia import MensagemNormalizada
from disparo.resposta import processar

RNG = random.Random(3)
AGORA = datetime(2026, 8, 4, 11, 0)
CFG = SimpleNamespace(vendedora_telefone="5511900000000",
                      equipe_telefone="5511900000000",
                      modelo_triagem="claude-haiku-4-5",
                      modelo_fechamento="claude-sonnet-5")


class EvoFalsa:
    def __init__(self):
        self.enviados: list[tuple[str, str]] = []
        self.lidas: list[str] = []
        self.digitou: list[float] = []

    def enviar_texto(self, telefone, texto):
        self.enviados.append((telefone, texto))
        return f"WA{len(self.enviados)}"

    def marcar_lida(self, telefone, wa_message_id):
        self.lidas.append(wa_message_id)

    def digitando(self, telefone, segundos):
        self.digitou.append(segundos)


def _claude(qualificacao: Qualificacao):
    return SimpleNamespace(
        messages=SimpleNamespace(
            parse=lambda **kw: SimpleNamespace(parsed_output=qualificacao)
        )
    )


def _msg(texto="tudo bem e vc", wa_id="WA-in-1"):
    return MensagemNormalizada("texto", texto, "5511988884444", wa_id)


def _q(decisao="continuar", resposta="Tudo bem também."):
    return Qualificacao(resposta=resposta, decisao=decisao, resumo="resumo",
                        paga_hoje=None, tem_cobertura="nao_informado",
                        carro_quitado="nao_informado")


def test_responde_e_vai_para_em_conversa(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    assert evo.enviados[0][0] == "5511988884444"
    assert status_de(conn, lead) == Status.EM_CONVERSA
    assert evo.lidas == ["WA-in-1"]
    assert evo.digitou


def test_escalar_avisa_a_equipe_e_silencia(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q("escalar", "Vou te passar pra equipe.")),
              CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    destinos = [d for d, _ in evo.enviados]
    assert "5511900000000" in destinos
    assert status_de(conn, lead) == Status.ESCALADO


def test_opt_out_entra_na_blocklist(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q("opt_out", "Tranquilo, não te incomodo mais.")),
              CFG, _msg("para de mandar"), AGORA, RNG, dormir=lambda s: None)
    assert status_de(conn, lead) == Status.OPT_OUT
    assert esta_bloqueado(conn, "5511988884444") is True


def test_lead_em_estado_terminal_e_ignorado(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.FRIO, AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    assert evo.enviados == []


def test_mensagem_duplicada_nao_responde_duas_vezes(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    for _ in range(2):
        processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG,
                  dormir=lambda s: None)
    assert len(evo.enviados) == 1


def test_telefone_desconhecido_e_ignorado(conn):
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG, dormir=lambda s: None)
    assert evo.enviados == []


def test_pausado_grava_mas_nao_responde(conn, lead):
    from disparo.disjuntor import pausar
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    pausar(conn, "teste", AGORA)
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None)
    assert evo.enviados == []
    total = conn.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0]
    assert total == 1  # a entrada foi gravada


def test_fase_de_fechamento_usa_sonnet(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    transicionar(conn, lead, Status.NEGOCIANDO, AGORA)
    modelos = []

    def parse(**kw):
        modelos.append(kw["model"])
        from types import SimpleNamespace
        return SimpleNamespace(parsed_output=_q(), content=[])

    cliente = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    processar(conn, EvoFalsa(), cliente, CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None)
    assert modelos == ["claude-sonnet-5"]


def test_duas_falhas_do_powercrm_escalam(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)

    class PowerQuebrado:
        def cotar(self, *a):
            from disparo.powercrm import PowerCRMIndisponivel
            raise PowerCRMIndisponivel("503")

        def gerar_cobranca(self, *a):
            from disparo.powercrm import PowerCRMIndisponivel
            raise PowerCRMIndisponivel("503")

    from types import SimpleNamespace as NS
    respostas = iter([
        NS(parsed_output=None, content=[
            NS(type="tool_use", id="t1", name="cotar", input={"placa": "A"})]),
        NS(parsed_output=None, content=[
            NS(type="tool_use", id="t2", name="cotar", input={"placa": "A"})]),
        NS(parsed_output=_q(), content=[]),
    ])
    cliente = NS(messages=NS(parse=lambda **kw: next(respostas)))
    evo = EvoFalsa()
    processar(conn, evo, cliente, CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None, powercrm=PowerQuebrado())
    assert status_de(conn, lead) == Status.ESCALADO
