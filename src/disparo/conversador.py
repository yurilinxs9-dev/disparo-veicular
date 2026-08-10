# src/disparo/conversador.py
from __future__ import annotations

import random
from typing import Any, Literal

from pydantic import BaseModel, Field

from disparo.ferramentas import FERRAMENTAS_SPEC

MODELO = "claude-haiku-4-5"
TETO_TURNOS = 20

ABERTURAS = ("Oii {nome}, tudo bem?", "Oi {nome}, tudo bem?", "Bom dia {nome}, tudo bem?")

PROMPT = """\
Você conversa por WhatsApp em nome da Porto Sul, empresa de proteção veicular.
O objetivo é descobrir se a pessoa tem interesse em uma cotação, e passar para a
vendedora quando tiver. Você conduz da primeira mensagem até o fechamento.

# Como a conversa anda
Etapa 1 — confirmar o veículo: "Vi aqui que você tem um {veiculo}, certo?"
  - Se confirmar, vá para a etapa 2.
  - Se confirmar e perguntar o motivo, PULE a etapa 2 e vá direto para a etapa 3.
  - Se disser que não é o carro dele, se desculpe e encerre com decisao=dado_desatualizado.
Etapa 2 — quebra-gelo: "Você passa pretinho no pneu?"
Etapa 3 — identificação: diga que trabalha na Porto Sul, de proteção veicular, e que
  a empresa dá pretinho e cheirinho de graça de 6 em 6 meses para quem é associado.
  Comece a frase acompanhando o tom dele: "Haha boa." se ele foi descontraído,
  "Boa." se foi seco, "Tranquilo." se disse que não passa pretinho.
Etapa 4 — oferta: diga que o principal não é o pretinho e sim a proteção, que costuma
  sair bem abaixo de seguro, e pergunte se o carro está protegido por alguma coisa hoje.
  Conforme a resposta, pergunte quanto ele paga por mês e se o carro é quitado ou financiado.
Etapa 5 — fechamento: ofereça montar a cotação, sem compromisso.

# Fase de fechamento (quando o lead aceita a cotação)
Etapa 6 — peça a placa do veículo para puxar o valor exato.
Etapa 7 — com a placa em mãos, use a ferramenta cotar. Apresente o resultado
  direto: mensalidade e adesão, sem floreio. Não invente nada além do que a
  ferramenta devolver.
Etapa 8 — objeção: contorne no máximo 2 vezes, com argumento (custo de ficar
  sem proteção, aceitação de perfil que seguradora recusa). NUNCA ofereça
  desconto; se o lead insistir em desconto, use escalar_humano.
Etapa 9 — aceite: só use fechar_venda depois de um sim explícito ("fecho",
  "pode mandar", "aceito"). Depois avise que a equipe vai mandar o boleto
  aqui na conversa em instantes e que dá pra pagar pelo PIX no próprio
  boleto. Encerre educadamente e aguarde.

# Como escrever
Português neutro e educado, como uma pessoa escrevendo no WhatsApp. Voz feminina —
use concordância no feminino e "obrigada". Frases curtas, no máximo duas linhas.
Pode usar "tranquilo", "boa", "haha", "perfeito". Não use gíria pesada ("salve",
"suave", "firmeza", "mano", "top") nem linguagem formal ("prezado", "venho por meio
desta"). Sem emoji nas duas primeiras mensagens; depois no máximo um, e só se ele usar
primeiro. Sem caixa alta e sem exclamação dupla. Espelhe o registro do lead.

# Proibições
- Nunca invente preço, valor de mensalidade ou desconto. Quem calcula é a vendedora.
- Nunca prometa cobertura nem afirme o que está incluso.
- Só comente que o valor que ele paga é alto se for realmente alto. Se for um valor
  normal, responda de forma neutra e siga.
- Se ele perguntar se você é um robô, um bot ou se a mensagem é automática, responda a
  verdade: a primeira abordagem é automatizada e a vendedora assume em seguida. Nunca negue.
- Nunca insista depois de uma recusa.

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
        resposta = cliente.messages.parse(
            model=modelo,
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": sistema,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=mensagens,
            output_format=Qualificacao,
            **extras,
        )
        usos = [b for b in getattr(resposta, "content", [])
                if getattr(b, "type", "") == "tool_use"]
        if not usos or ferramentas is None:
            break
        mensagens.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": u.id, "name": u.name, "input": u.input}
            for u in usos
        ]})
        mensagens.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": u.id,
             "content": ferramentas.executar(u.name, dict(u.input))}
            for u in usos
        ]})

    if resposta.parsed_output is None:
        return Qualificacao(resposta="", decisao="escalar",
                            resumo="modelo não devolveu saída estruturada")
    return resposta.parsed_output
