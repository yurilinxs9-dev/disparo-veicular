import random
from datetime import datetime
from types import SimpleNamespace

from disparo.blocklist import esta_bloqueado
from disparo.conversador import Qualificacao
from disparo.fila import FilaPorLead
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


def _resposta_final(qualificacao: Qualificacao):
    return SimpleNamespace(content=[SimpleNamespace(
        type="text", text=qualificacao.model_dump_json())])


def _claude(qualificacao: Qualificacao):
    return SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kw: _resposta_final(qualificacao)
        )
    )


def _msg(texto="tudo bem e vc", wa_id="WA-in-1"):
    return MensagemNormalizada("texto", texto, "5511988884444", wa_id)


def _q(decisao="continuar", resposta="Tudo bem também."):
    return Qualificacao(resposta=resposta, decisao=decisao, resumo="resumo",
                        paga_hoje=None, tem_cobertura="nao_informado",
                        carro_quitado="nao_informado")


class ClaudeContador:
    """Fake que conta chamadas e permite injetar efeito colateral por chamada."""

    def __init__(self, qualificacao, ao_chamar=None):
        self.chamadas = 0
        self._q = qualificacao
        self._ao_chamar = ao_chamar
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.chamadas += 1
        if self._ao_chamar:
            self._ao_chamar(self.chamadas)
        return _resposta_final(self._q)


def test_bloco_de_mensagens_rapidas_gera_uma_resposta_so(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q())
    # a janela de debounce é sempre a PRIMEIRA chamada de dormir da rodada;
    # usamos um contador de chamadas em vez do valor de segundos sorteado
    # (nota do brief) para simular a segunda mensagem chegando durante ela.
    chamadas_dormir = []

    def dormir_rodada1(s):
        chamadas_dormir.append(s)
        if len(chamadas_dormir) == 1:
            fila.chegou(lead)

    # simula duas mensagens: a primeira desiste no debounce, a segunda responde
    processar(conn, evo, claude, CFG, _msg("Sim", "WA-b1"), AGORA, RNG,
              dormir=dormir_rodada1, fila=fila)
    # rodada 1 morreu no aguardar (chegou WA-b2 durante a janela); agora a rodada 2:
    processar(conn, evo, claude, CFG, _msg("Por que?", "WA-b2"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert claude.chamadas == 1          # uma geração para o bloco inteiro
    assert len({t for t, _ in evo.enviados}) == 1


def test_regenera_quando_chega_mensagem_durante_a_geracao(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()

    def chega_no_meio(chamada):
        if chamada == 1:  # simula mensagem nova enquanto o modelo gerava
            fila.chegou(lead)
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, "
                "wa_message_id, criado_em) VALUES (?, 'entrada', 'texto', "
                "'e outra coisa', 'WA-r2', ?)", (lead, AGORA.isoformat()))
            conn.commit()

    claude = ClaudeContador(_q(), ao_chamar=chega_no_meio)
    processar(conn, evo, claude, CFG, _msg("primeira", "WA-r1"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert claude.chamadas == 2          # 1ª descartada, regenerou


def test_regeneracao_para_na_terceira_tentativa(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q(), ao_chamar=lambda n: fila.chegou(lead))
    processar(conn, evo, claude, CFG, _msg("oi", "WA-t1"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert claude.chamadas == 3          # original + 2 regenerações, depois envia
    assert evo.enviados                  # enviou mesmo com fila mudando sempre


def test_saida_gravada_com_hora_real_do_envio(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    envio = datetime(2026, 8, 4, 11, 2, 30)
    processar(conn, evo, _claude(_q()), CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None, agora_envio=lambda: envio)
    linha = conn.execute(
        "SELECT criado_em FROM mensagens WHERE direcao='saida'").fetchone()
    assert linha["criado_em"] == envio.isoformat()


def test_marca_lida_a_ultima_entrada_do_banco(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, tipo, texto, wa_message_id, "
        "criado_em) VALUES (?, 'entrada', 'texto', 'antiga', 'WA-old', ?)",
        (lead, AGORA.isoformat()))
    conn.commit()
    evo = EvoFalsa()
    processar(conn, evo, _claude(_q()), CFG, _msg("nova", "WA-new"), AGORA, RNG,
              dormir=lambda s: None)
    assert evo.lidas == ["WA-new"]


def test_nao_responde_se_historico_termina_em_saida(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q())
    # a 2ª chamada de dormir é a de atraso_leitura, já dentro de _responder,
    # com o lead ocupado pelo laço assumir/liberar de `processar`: simula uma
    # rodada concorrente tentando assumir, que fica pendente e força uma
    # reprocessagem — mas o histórico já termina em 'saida' nessa hora.
    chamadas_dormir = []

    def dormir_com_pendencia(s):
        chamadas_dormir.append(s)
        if len(chamadas_dormir) == 2:
            fila.assumir(lead)

    processar(conn, evo, claude, CFG, _msg("oi", "WA-g1"), AGORA, RNG,
              dormir=dormir_com_pendencia, fila=fila)
    # a rodada pendente reprocessou, mas o guard de histórico terminando em
    # 'saida' evitou gerar (e enviar) de novo.
    assert claude.chamadas == 1
    assert len(evo.enviados) == 1


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


def test_responde_quando_jid_vem_sem_nono_digito(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    msg = MensagemNormalizada("texto", "tudo bem e vc", "551188884444", "WA-alt-1")
    processar(conn, evo, _claude(_q()), CFG, msg, AGORA, RNG, dormir=lambda s: None)
    assert evo.enviados  # lead 5511988884444 casou com o JID sem o nono dígito
    assert status_de(conn, lead) == Status.EM_CONVERSA


def test_opt_out_bloqueia_o_telefone_do_cadastro(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    msg = MensagemNormalizada("texto", "para de mandar", "551188884444", "WA-alt-2")
    processar(conn, evo, _claude(_q("opt_out", "Tranquilo.")), CFG, msg, AGORA, RNG,
              dormir=lambda s: None)
    assert esta_bloqueado(conn, "5511988884444") is True  # bloqueia o E164 do lead


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

    def create(**kw):
        modelos.append(kw["model"])
        return _resposta_final(_q())

    cliente = SimpleNamespace(messages=SimpleNamespace(create=create))
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

    from types import SimpleNamespace as NS
    respostas = iter([
        NS(content=[
            NS(type="tool_use", id="t1", name="cotar", input={"placa": "A"})]),
        NS(content=[
            NS(type="tool_use", id="t2", name="cotar", input={"placa": "A"})]),
        _resposta_final(_q()),
    ])
    cliente = NS(messages=NS(create=lambda **kw: next(respostas)))
    evo = EvoFalsa()
    processar(conn, evo, cliente, CFG, _msg(), AGORA, RNG,
              dormir=lambda s: None, powercrm=PowerQuebrado())
    assert status_de(conn, lead) == Status.ESCALADO


def test_falhas_powercrm_nao_sobrescreve_opt_out(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)

    class PowerQuebrado:
        def cotar(self, *a):
            from disparo.powercrm import PowerCRMIndisponivel
            raise PowerCRMIndisponivel("503")

    from types import SimpleNamespace as NS
    respostas = iter([
        NS(content=[
            NS(type="tool_use", id="t1", name="cotar", input={"placa": "A"})]),
        NS(content=[
            NS(type="tool_use", id="t2", name="cotar", input={"placa": "A"})]),
        _resposta_final(_q("opt_out", "Para de mandar mensagem.")),
    ])
    cliente = NS(messages=NS(create=lambda **kw: next(respostas)))
    evo = EvoFalsa()
    processar(conn, evo, cliente, CFG, _msg("para de mandar"), AGORA, RNG,
              dormir=lambda s: None, powercrm=PowerQuebrado())
    assert status_de(conn, lead) == Status.OPT_OUT
    assert esta_bloqueado(conn, "5511988884444") is True


def test_fechamento_avisa_equipe(conn, lead):
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)

    from disparo.powercrm import Cotacao

    class PowerOk:
        def cotar(self, nome, telefone, placa):
            return Cotacao("QTN-1", "NEG-1", "Master", "189,90", "250,00")

    from types import SimpleNamespace as NS
    respostas = iter([
        NS(content=[
            NS(type="tool_use", id="t1", name="cotar",
               input={"placa": "ABC1D23"})]),
        NS(content=[
            NS(type="tool_use", id="t2", name="fechar_venda", input={})]),
        _resposta_final(_q(resposta="Fechado, o boleto chega em instantes.")),
    ])
    cliente = NS(messages=NS(create=lambda **kw: next(respostas)))
    evo = EvoFalsa()
    processar(conn, evo, cliente, CFG, _msg("fecho sim"), AGORA, RNG,
              dormir=lambda s: None, powercrm=PowerOk())
    assert status_de(conn, lead) == Status.AGUARDANDO_PAGAMENTO
    texto_equipe = next(
        t for d, t in evo.enviados if d == CFG.equipe_telefone)
    assert "VENDA FECHADA" in texto_equipe
    assert "QTN-1" in texto_equipe


# --- Testes de regressão da revisão da Task 3 (C1, C2, I1-I5) --------------

def test_mensagem_durante_envio_nao_e_engolida(conn, lead):
    """C1: mensagem que chega depois da geração, mas antes do envio, não
    pode ser perdida pelo guard de histórico terminando em 'saida'."""
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q())
    chamadas_dormir = []

    def dormir_com_mensagem_durante_envio(s):
        chamadas_dormir.append(s)
        if len(chamadas_dormir) == 3:  # atraso_resposta: já gerou, ainda não enviou
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, "
                "wa_message_id, criado_em) VALUES (?, 'entrada', 'texto', "
                "'ainda ai?', 'WA-c1-b', ?)", (lead, AGORA.isoformat()))
            conn.commit()
            fila.chegou(lead)      # rodada de B chega e reinicia o sequencial
            fila.assumir(lead)     # concorrente: A ainda segura a trava, B fica pendente

    processar(conn, evo, claude, CFG, _msg("oi", "WA-c1-a"), AGORA, RNG,
              dormir=dormir_com_mensagem_durante_envio, fila=fila)
    assert claude.chamadas == 2      # respondeu A e, na reprocessagem, B também
    assert len(evo.enviados) == 2


def test_regeneracao_nao_reexecuta_ferramenta_com_efeito_colateral(conn, lead):
    """C2: uma tentativa descartada não pode repetir uma ferramenta que já
    causou efeito externo (aqui, uma cotação no PowerCRM)."""
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()

    from disparo.powercrm import Cotacao

    chamadas_cotar = []

    class PowerContador:
        def cotar(self, nome, telefone, placa):
            chamadas_cotar.append(placa)
            return Cotacao("QTN-1", "NEG-1", "Master", "189,90", "250,00")

    from types import SimpleNamespace as NS
    contagem = {"n": 0}

    def criar(**kw):
        contagem["n"] += 1
        n = contagem["n"]
        if n == 1:
            # simula mensagem nova chegando durante a 1ª tentativa, depois
            # que a ferramenta com efeito externo já rodou
            fila.chegou(lead)
        if n % 2 == 1:  # a 1ª chamada de cada tentativa pede a cotação
            return NS(content=[NS(type="tool_use", id=f"t{n}", name="cotar",
                                   input={"placa": "ABC1D23"})])
        return _resposta_final(_q(resposta="Fica R$189,90 por mês."))

    cliente = NS(messages=NS(create=criar))
    processar(conn, evo, cliente, CFG, _msg("quero cotar", "WA-c2"), AGORA, RNG,
              dormir=lambda s: None, fila=fila, powercrm=PowerContador())
    assert chamadas_cotar == ["ABC1D23"]   # cotou uma única vez, sem duplicar
    assert len(evo.enviados) == 1


def test_falha_de_ferramenta_nao_acumula_entre_tentativas_descartadas(conn, lead):
    """I1: falha de ferramenta de uma tentativa descartada não pode se somar
    à de uma tentativa seguinte e forçar uma escalada indevida."""
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    transicionar(conn, lead, Status.EM_CONVERSA, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()

    class PowerSempreFalha:
        def cotar(self, *a):
            from disparo.powercrm import PowerCRMIndisponivel
            raise PowerCRMIndisponivel("503")

    from types import SimpleNamespace as NS
    contagem = {"n": 0}

    def criar(**kw):
        contagem["n"] += 1
        n = contagem["n"]
        if n == 1:
            fila.chegou(lead)   # mensagem nova durante a 1ª tentativa
        if n % 2 == 1:
            return NS(content=[NS(type="tool_use", id=f"t{n}", name="cotar",
                                   input={"placa": "ABC1D23"})])
        return _resposta_final(_q())

    cliente = NS(messages=NS(create=criar))
    processar(conn, evo, cliente, CFG, _msg("quero cotar", "WA-i1"), AGORA, RNG,
              dormir=lambda s: None, fila=fila, powercrm=PowerSempreFalha())
    assert status_de(conn, lead) != Status.ESCALADO


def test_tira_midia_mesmo_quando_a_rodada_aborta_cedo(conn, lead):
    """I2: a mídia pendente não pode vazar da fila para uma mensagem futura
    quando a rodada aborta antes de usá-la (aqui, pelo disjuntor pausado)."""
    from disparo.disjuntor import pausar
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    pausar(conn, "teste", AGORA)
    msg_com_imagem = MensagemNormalizada(
        "imagem", "[foto]", "5511988884444", "WA-i2-a",
        imagem_b64="fotoantiga", media_type="image/jpeg")
    processar(conn, evo, _claude(_q()), CFG, msg_com_imagem, AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert fila.tirar_midia(lead) is None  # não pode sobrar imagem na fila


def test_recheca_robo_pode_falar_apos_a_espera(conn, lead):
    """I3: se o lead sair do estado que permite o robô falar enquanto a
    rodada espera a trava, a rodada não pode responder mesmo assim."""
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q())
    feito = []

    def dormir_com_opt_out_concorrente(s):
        if not feito:
            feito.append(True)
            from disparo.blocklist import bloquear
            transicionar(conn, lead, Status.OPT_OUT, AGORA)
            bloquear(conn, "5511988884444", "opt_out", AGORA)

    processar(conn, evo, claude, CFG, _msg("oi", "WA-i3"), AGORA, RNG,
              dormir=dormir_com_opt_out_concorrente, fila=fila)
    assert evo.enviados == []


def test_turnos_conta_certo_entre_reprocessamentos(conn, lead):
    """I4: cada rodada reprocessada precisa reler o lead do banco, senão
    turnos fica subcontado (a rodada 2 recalcula em cima do snapshot velho
    da rodada 1)."""
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    claude = ClaudeContador(_q())
    chamadas_dormir = []

    def dormir_com_pendencia(s):
        chamadas_dormir.append(s)
        if len(chamadas_dormir) == 3:  # atraso_resposta: rodada 1 já gerou
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, "
                "wa_message_id, criado_em) VALUES (?, 'entrada', 'texto', "
                "'mais uma coisa', 'WA-i4-b', ?)", (lead, AGORA.isoformat()))
            conn.commit()
            fila.chegou(lead)
            fila.assumir(lead)  # concorrente: fica pendente

    processar(conn, evo, claude, CFG, _msg("oi", "WA-i4-a"), AGORA, RNG,
              dormir=dormir_com_pendencia, fila=fila)
    turnos = conn.execute(
        "SELECT turnos FROM leads WHERE id = ?", (lead,)).fetchone()["turnos"]
    assert turnos == 2


def test_excecao_em_responder_nao_perde_a_pendencia(conn, lead):
    """I5: uma exceção numa rodada não pode descartar a pendência que já
    tinha se formado — a próxima volta do laço tem que consumi-la."""
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()

    class ClaudeComFalhaNaPrimeira:
        def __init__(self):
            self.chamadas = 0
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kw):
            self.chamadas += 1
            if self.chamadas == 1:
                fila.assumir(lead)  # concorrente: fica pendente antes da falha
                raise RuntimeError("timeout simulado do modelo")
            return _resposta_final(_q())

    claude = ClaudeComFalhaNaPrimeira()
    processar(conn, evo, claude, CFG, _msg("oi", "WA-i5"), AGORA, RNG,
              dormir=lambda s: None, fila=fila)
    assert len(evo.enviados) == 1  # a rodada pendente respondeu mesmo assim
    alerta = conn.execute(
        "SELECT texto FROM eventos WHERE tipo = 'alerta' ORDER BY id DESC "
        "LIMIT 1").fetchone()
    assert "timeout simulado" in alerta["texto"]


def test_mensagem_durante_envio_nao_termina_prompt_em_turno_assistente(conn, lead):
    """C3: o fix de C1 não pode montar uma lista de `messages` terminando
    em turno assistant — isso ativa "prefill" na API, incompatível com
    saída estruturada (e quebra a fase de fechamento com 400)."""
    transicionar(conn, lead, Status.CONTATADO, AGORA)
    evo = EvoFalsa()
    fila = FilaPorLead()
    chamadas_messages = []

    def criar(**kw):
        chamadas_messages.append(kw["messages"])
        return _resposta_final(_q())

    from types import SimpleNamespace as NS
    cliente = NS(messages=NS(create=criar))
    chamadas_dormir = []

    def dormir_com_mensagem_durante_envio(s):
        chamadas_dormir.append(s)
        if len(chamadas_dormir) == 3:  # atraso_resposta: já gerou, ainda não enviou
            conn.execute(
                "INSERT INTO mensagens (lead_id, direcao, tipo, texto, "
                "wa_message_id, criado_em) VALUES (?, 'entrada', 'texto', "
                "'ainda ai?', 'WA-c3-b', ?)", (lead, AGORA.isoformat()))
            conn.commit()
            fila.chegou(lead)
            fila.assumir(lead)  # concorrente: A ainda segura a trava

    processar(conn, evo, cliente, CFG, _msg("oi", "WA-c3-a"), AGORA, RNG,
              dormir=dormir_com_mensagem_durante_envio, fila=fila)

    assert len(chamadas_messages) == 2  # respondeu A e, na reprocessagem, B
    ultima_chamada = chamadas_messages[-1]
    assert ultima_chamada[-1]["role"] == "user"  # nunca pode terminar em assistant
    textos_finais = [b["text"] for b in ultima_chamada[-1]["content"]
                     if b.get("type") == "text"]
    assert any("ainda ai?" in t for t in textos_finais)
