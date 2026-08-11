# tests/test_conversador.py
import random
from types import SimpleNamespace

import pytest

from disparo.conversador import (ABERTURAS, PROMPT, Qualificacao, abertura, conversar)


class ClienteFalso:
    """Duplo do cliente Anthropic. Guarda a chamada e devolve o que mandarmos."""

    def __init__(self, resultado: Qualificacao):
        self.resultado = resultado
        self.chamada: dict | None = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.chamada = kwargs
        return SimpleNamespace(content=[SimpleNamespace(
            type="text", text=self.resultado.model_dump_json())])


LEAD = {"nome": "Joao", "veiculo": "Onix 2019", "turnos": 2}
HISTORICO = [
    {"direcao": "saida", "texto": "Oii Joao, tudo bem?"},
    {"direcao": "entrada", "texto": "tudo bem e vc"},
]


def test_abertura_usa_o_nome_e_varia():
    rng = random.Random(1)
    textos = {abertura("Joao", rng) for _ in range(50)}
    assert len(textos) >= 2
    assert all("Joao" in t for t in textos)
    assert all("Porto Sul" not in t for t in textos)


def test_conversar_usa_o_modelo_certo_e_cacheia_o_prompt():
    cliente = ClienteFalso(Qualificacao(
        resposta="Tudo bem também.", decisao="continuar", resumo="",
        paga_hoje=None, tem_cobertura="nao_informado", carro_quitado="nao_informado",
    ))
    conversar(cliente, LEAD, HISTORICO)
    chamada = cliente.chamada
    assert chamada["model"] == "claude-haiku-4-5"
    assert chamada["system"][0]["cache_control"] == {"type": "ephemeral"}
    esquema = chamada["output_config"]["format"]["schema"]
    assert esquema["additionalProperties"] is False
    assert set(esquema["required"]) == set(esquema["properties"].keys())


def test_historico_vira_papeis_alternados():
    cliente = ClienteFalso(Qualificacao(
        resposta="ok", decisao="continuar", resumo="",
        paga_hoje=None, tem_cobertura="nao_informado", carro_quitado="nao_informado",
    ))
    conversar(cliente, LEAD, HISTORICO)
    mensagens = cliente.chamada["messages"]
    assert mensagens[0]["role"] == "assistant"
    assert mensagens[1]["role"] == "user"


def test_imagem_entra_como_bloco_de_imagem():
    cliente = ClienteFalso(Qualificacao(
        resposta="ok", decisao="continuar", resumo="",
        paga_hoje=None, tem_cobertura="nao_informado", carro_quitado="nao_informado",
    ))
    historico = HISTORICO + [{
        "direcao": "entrada", "texto": "[o lead enviou uma foto]",
        "imagem_b64": "QUJD", "media_type": "image/jpeg",
    }]
    conversar(cliente, LEAD, historico)
    ultimo = cliente.chamada["messages"][-1]["content"]
    assert any(bloco["type"] == "image" for bloco in ultimo)


def test_prompt_contem_as_regras_criticas():
    for trecho in ["Porto Sul", "pretinho", "nunca invente", "automat"]:
        assert trecho.lower() in PROMPT.lower()


def test_decisao_quente_saiu_do_schema():
    from disparo.conversador import Qualificacao
    import pytest
    with pytest.raises(Exception):
        Qualificacao(resposta="x", decisao="quente", resumo="r")


def test_laco_executa_ferramenta_e_volta():
    from types import SimpleNamespace
    from disparo.conversador import Qualificacao, conversar

    chamadas = []

    class FerramentasFalsas:
        escalou = False
        falhas_powercrm = 0

        def executar(self, nome, entrada):
            chamadas.append((nome, entrada))
            return "plano Master: mensalidade R$ 189.90, adesao R$ 250.00"

    q = Qualificacao(resposta="Fica R$ 189,90 por mês.", decisao="continuar",
                     resumo="cotado")
    respostas = iter([
        SimpleNamespace(  # 1a chamada: modelo pede a ferramenta (com texto solto)
            content=[SimpleNamespace(type="text", text="Vou cotar."),
                     SimpleNamespace(type="tool_use", id="t1", name="cotar",
                                     input={"placa": "ABC1D23"})],
        ),
        SimpleNamespace(content=[SimpleNamespace(  # 2a: saída final
            type="text", text=q.model_dump_json())]),
    ])
    cliente = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: next(respostas)))
    lead = {"nome": "Joao", "veiculo": "Onix 2019"}
    resultado = conversar(cliente, lead, [
        {"direcao": "entrada", "texto": "pode cotar, placa ABC1D23"},
    ], ferramentas=FerramentasFalsas(), modelo="claude-sonnet-5")
    assert chamadas == [("cotar", {"placa": "ABC1D23"})]
    assert resultado.decisao == "continuar"
    assert "189,90" in resultado.resposta


def test_sem_ferramentas_nao_manda_tools():
    from types import SimpleNamespace
    from disparo.conversador import Qualificacao, conversar
    capturado = {}

    def create(**kw):
        capturado.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(
            type="text", text=Qualificacao(
                resposta="oi", decisao="continuar", resumo="r").model_dump_json())])

    cliente = SimpleNamespace(messages=SimpleNamespace(create=create))
    conversar(cliente, {"nome": "J", "veiculo": "Onix"},
              [{"direcao": "entrada", "texto": "oi"}])
    assert "tools" not in capturado or not capturado["tools"]


def test_texto_fora_do_esquema_vira_escalada():
    from types import SimpleNamespace
    from disparo.conversador import conversar
    cliente = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(content=[SimpleNamespace(
            type="text", text="nao e json")])))
    r = conversar(cliente, {"nome": "J", "veiculo": "Onix"},
                  [{"direcao": "entrada", "texto": "oi"}])
    assert r.decisao == "escalar"
    assert r.resposta == ""


def test_aberturas_tem_12_variacoes_unicas_com_nome():
    assert len(ABERTURAS) >= 12
    assert len(set(ABERTURAS)) == len(ABERTURAS)
    assert all("{nome}" in a for a in ABERTURAS)
    assert all(not a.endswith(".") for a in ABERTURAS)


def test_prompt_tem_regras_duras_e_anti_repeticao():
    assert "Nunca invente preço" in PROMPT
    assert "NUNCA repita" in PROMPT
    assert "não é roteiro fixo" in PROMPT
