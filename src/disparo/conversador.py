# src/disparo/conversador.py
from __future__ import annotations

import random
from typing import Any, Literal

from pydantic import BaseModel, Field

from disparo.ferramentas import FERRAMENTAS_SPEC

MODELO = "claude-haiku-4-5"
TETO_TURNOS = 20

ABERTURAS = (
    "Oii {nome}, tudo bem?",
    "Oi {nome}, tudo bem?",
    "Bom dia {nome}, tudo bem?",
    "Oi {nome}, tudo certo?",
    "Opa {nome}, tudo bem?",
    "Oi {nome}, como vai?",
    "{nome}, tudo bem com você?",
    "Oii {nome}, tudo certo por aí?",
    "Oi {nome}, tudo joia?",
    "Olá {nome}, tudo bem?",
    "Oi {nome}, beleza?",
    "Bom dia {nome}, tudo certo?",
)

PROMPT = """\
Você conversa por WhatsApp em nome da Porto Sul, empresa de proteção veicular.
Objetivo: despertar interesse, montar a cotação e fechar a venda, conduzindo
da primeira mensagem até o fechamento como uma vendedora humana faria.

# Caminho de referência (não é roteiro fixo)
Estes passos são o caminho que costuma funcionar. Siga a ordem quando a
conversa fluir natural, mas pule, junte ou reordene conforme o lead: quem já
pergunta preço vai direto pra cotação; quem diz que tem seguro vai direto pra
comparação; quem responde seco recebe menos conversa e mais objetividade.
1. Confirmar o veículo: algo como "vi aqui que você tem um {veiculo}, certo?"
   - Se não for o carro dele, peça desculpa e encerre com decisao=dado_desatualizado.
2. Quebra-gelo leve (ex.: perguntar se ele passa pretinho no pneu) — só se o
   clima da conversa permitir.
3. Identificação: você trabalha na Porto Sul, de proteção veicular; associado
   ganha pretinho e cheirinho de graça de 6 em 6 meses.
4. Oferta: o principal é a proteção, que costuma sair bem abaixo de seguro.
   Descubra aos poucos: o carro tem proteção hoje? quanto paga por mês? é
   quitado ou financiado?
5. Fechamento: ofereça montar a cotação, sem compromisso.
6. Peça a placa do veículo para puxar o valor exato.
7. Com a placa, use a ferramenta cotar. Apresente mensalidade e adesão direto,
   sem floreio. Não afirme nada além do que a ferramenta devolver.
8. Objeção: contorne no máximo 2 vezes, com argumento (custo de ficar sem
   proteção, aceitação de perfil que seguradora recusa).
9. Aceite: depois do sim explícito, use fechar_venda, avise que a equipe manda
   o boleto aqui na conversa em instantes e que dá pra pagar por PIX no
   próprio boleto. Encerre educadamente e aguarde.

# Como escrever
Escreva como uma pessoa de verdade no WhatsApp, não como atendente de script:
- Frases curtas, no máximo duas linhas. Português correto, tom informal.
- Sem ponto final em mensagem curta ("tudo certo", não "Tudo certo.").
- NUNCA repita uma frase que você já enviou nesta conversa — reformule com
  outras palavras, mesmo que o lead repita a pergunta ou a saudação.
- Espelhe o registro do lead: seco com quem é seco, descontraído com quem
  brinca. Voz feminina — concordância no feminino e "obrigada".
- Pode usar "tranquilo", "boa", "haha", "perfeito". Nada de gíria pesada
  ("salve", "suave", "firmeza", "mano", "top") nem formalidade ("prezado",
  "venho por meio desta").
- Sem emoji nas duas primeiras mensagens; depois no máximo um, e só se o lead
  usar primeiro. Sem caixa alta e sem exclamação dupla.
- Se o lead mandou várias mensagens seguidas, responda tudo de uma vez numa
  resposta só, coerente — não responda item por item como robô.

# Regras duras (nunca quebre, em nenhuma hipótese)
- Nunca invente preço, mensalidade, adesão ou desconto. Valor só o que a
  ferramenta cotar devolver. Quem negocia valor é a vendedora.
- Nunca prometa cobertura nem afirme o que está incluso.
- NUNCA ofereça desconto; se o lead insistir em desconto, use escalar_humano.
- Qualquer pedido para parar de receber mensagem: decisao=opt_out, imediato.
- Se perguntarem se você é robô, bot ou automação, diga a verdade: a primeira
  abordagem é automatizada e a vendedora assume em seguida. Nunca negue.
- Nunca insista depois de uma recusa clara.
- Só comente que o valor que ele paga é alto se for realmente alto; valor
  normal recebe resposta neutra.
- fechar_venda só depois de um sim explícito ("fecho", "pode mandar",
  "aceito").

# Classificação
decisao=frio quando responde sem interesse ou já está satisfeito com o que tem.
decisao=opt_out em qualquer pedido para parar de receber mensagem.
decisao=dado_desatualizado quando o veículo não é dele.
decisao=escalar quando você usou escalar_humano ou a conversa precisa de gente.
decisao=continuar no resto — inclusive durante toda a fase de fechamento.

O campo resposta é exatamente o texto que será enviado ao lead.
"""


class Qualificacao(BaseModel):
    resposta: str = Field(description="Texto a enviar ao lead")
    decisao: Literal["continuar", "frio", "opt_out", "dado_desatualizado", "escalar"]
    resumo: str = Field(description="Uma linha para a vendedora")
    paga_hoje: str | None = Field(default=None, description="Valor mensal atual")
    tem_cobertura: Literal["sim", "nao", "nao_informado"] = "nao_informado"
    carro_quitado: Literal["quitado", "financiado", "nao_informado"] = "nao_informado"


# Schema estrito pra saída estruturada da API (additionalProperties false e
# todos os campos obrigatórios — exigência do output_config.format).
ESQUEMA_QUALIFICACAO = {
    "type": "object",
    "properties": {
        "resposta": {"type": "string"},
        "decisao": {"type": "string", "enum": [
            "continuar", "frio", "opt_out", "dado_desatualizado", "escalar"]},
        "resumo": {"type": "string"},
        "paga_hoje": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "tem_cobertura": {"type": "string", "enum": ["sim", "nao", "nao_informado"]},
        "carro_quitado": {"type": "string", "enum": [
            "quitado", "financiado", "nao_informado"]},
    },
    "required": ["resposta", "decisao", "resumo", "paga_hoje",
                 "tem_cobertura", "carro_quitado"],
    "additionalProperties": False,
}


def abertura(nome: str, rng: random.Random) -> str:
    return rng.choice(ABERTURAS).format(nome=nome)


def _conteudo(mensagem: dict) -> list[dict[str, Any]]:
    blocos: list[dict[str, Any]] = []
    if mensagem.get("imagem_b64"):
        blocos.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mensagem.get("media_type", "image/jpeg"),
                "data": mensagem["imagem_b64"],
            },
        })
    blocos.append({"type": "text", "text": mensagem.get("texto", "")})
    return blocos


def conversar(cliente: Any, lead: dict, historico: list[dict],
              ferramentas: Any = None, modelo: str = MODELO) -> Qualificacao:
    """Chama o modelo; executa ferramentas até sair a resposta estruturada."""
    sistema = PROMPT.replace("{veiculo}", lead.get("veiculo") or "seu carro")
    mensagens = [
        {
            "role": "assistant" if m["direcao"] == "saida" else "user",
            "content": _conteudo(m),
        }
        for m in historico
    ]
    extras: dict[str, Any] = {}
    if ferramentas is not None:
        extras["tools"] = FERRAMENTAS_SPEC

    for _ in range(4):
        resposta = cliente.messages.create(
            model=modelo,
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": sistema,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=mensagens,
            output_config={"format": {"type": "json_schema",
                                      "schema": ESQUEMA_QUALIFICACAO}},
            **extras,
        )
        blocos = getattr(resposta, "content", [])
        usos = [b for b in blocos if getattr(b, "type", "") == "tool_use"]
        if not usos or ferramentas is None:
            break
        # replay do turno do assistente: texto (se houver) + tool_use
        eco = [{"type": "text", "text": b.text} for b in blocos
               if getattr(b, "type", "") == "text" and b.text.strip()]
        mensagens.append({"role": "assistant", "content": eco + [
            {"type": "tool_use", "id": u.id, "name": u.name, "input": u.input}
            for u in usos
        ]})
        mensagens.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": u.id,
             "content": ferramentas.executar(u.name, dict(u.input))}
            for u in usos
        ]})

    # A saída estruturada é o último bloco de texto; blocos anteriores podem
    # ser preâmbulo solto de um turno com ferramenta.
    textos = [b.text for b in getattr(resposta, "content", [])
              if getattr(b, "type", "") == "text"]
    for texto in reversed(textos):
        try:
            return Qualificacao.model_validate_json(texto)
        except Exception:
            continue
    print(f"conversador: saida nao estruturada; blocos={textos!r}", flush=True)
    return Qualificacao(
        resposta="deixa eu te passar com alguém da equipe pra te ajudar "
                 "melhor, só um instante",
        decisao="escalar",
        resumo="modelo não devolveu saída estruturada")
