# src/disparo/ferramentas.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from disparo import eventos
from disparo.maquina import Status, status_de, transicionar
from disparo.powercrm import PowerCRMIndisponivel, PowerCRMRecusa

FERRAMENTAS_SPEC = [
    {
        "name": "cotar",
        "description": "Consulta o preço da proteção no Power CRM pela placa. "
                       "Use assim que tiver a placa do veículo.",
        "input_schema": {
            "type": "object",
            "properties": {"placa": {"type": "string"}},
            "required": ["placa"],
        },
    },
    {
        "name": "fechar_venda",
        "description": "Registra o aceite da venda e aciona a equipe pra gerar "
                       "e enviar o boleto (pagável por PIX). Use SOMENTE depois "
                       "de o cliente aceitar explicitamente o valor.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "escalar_humano",
        "description": "Passa a conversa para a equipe humana. Use em recusa "
                       "firme, pedido de desconto, ou assunto fora do escopo.",
        "input_schema": {
            "type": "object",
            "properties": {"motivo": {"type": "string"}},
            "required": ["motivo"],
        },
    },
]


class Ferramentas:
    def __init__(self, conn: sqlite3.Connection, powercrm, lead_id: int,
                 agora: datetime) -> None:
        self._conn = conn
        self._power = powercrm
        self._lead = lead_id
        self._agora = agora
        self.escalou = False
        self.fechou = False
        self.falhas_powercrm = 0
        self.chamadas = 0  # conta toda execução, p/ detectar efeito colateral por tentativa

    def executar(self, nome: str, entrada: dict) -> str:
        self.chamadas += 1
        if nome == "cotar":
            return self._cotar(entrada.get("placa", ""))
        if nome == "fechar_venda":
            return self._fechar_venda()
        if nome == "escalar_humano":
            return self._escalar(entrada.get("motivo", "sem motivo"))
        return f"erro: ferramenta desconhecida {nome}"

    def _linha(self) -> sqlite3.Row:
        return self._conn.execute(
            "SELECT * FROM leads WHERE id = ?", (self._lead,)).fetchone()

    def _cotar(self, placa: str) -> str:
        lead = self._linha()
        try:
            cot = self._power.cotar(lead["nome"], lead["telefone_e164"], placa)
        except PowerCRMIndisponivel as erro:
            self.falhas_powercrm += 1
            eventos.registrar(self._conn, "alerta",
                              f"Power CRM falhou na cotação: {erro}",
                              self._agora, self._lead)
            return "erro: sistema de cotacao fora do ar"
        except PowerCRMRecusa as erro:
            eventos.registrar(self._conn, "alerta",
                              f"Power CRM recusou a cotação: {erro}",
                              self._agora, self._lead)
            return f"erro: cotacao recusada ({erro})"
        self._conn.execute(
            "UPDATE leads SET placa = ?, cotacao_id = ?, negociacao_id = ?, "
            "plano = ?, mensalidade = ?, adesao = ? WHERE id = ?",
            (placa, cot.cotacao_id, cot.negociacao_id, cot.plano,
             cot.mensalidade, cot.adesao, self._lead),
        )
        self._conn.commit()
        if status_de(self._conn, self._lead) == Status.EM_CONVERSA:
            transicionar(self._conn, self._lead, Status.NEGOCIANDO, self._agora)
        eventos.registrar(self._conn, "sistema",
                          f"Cotação {cot.plano}: R$ {cot.mensalidade}/mês",
                          self._agora, self._lead)
        return (f"plano {cot.plano}: mensalidade R$ {cot.mensalidade}, "
                f"adesao R$ {cot.adesao}")

    def _fechar_venda(self) -> str:
        lead = self._linha()
        if not lead["cotacao_id"]:
            return "erro: nenhuma cotacao feita"
        status_atual = status_de(self._conn, self._lead)
        if status_atual == Status.AGUARDANDO_PAGAMENTO:
            return "venda ja registrada: a equipe esta cuidando do boleto"
        if status_atual != Status.NEGOCIANDO:
            return "erro: venda nao esta em negociacao"
        transicionar(self._conn, self._lead, Status.AGUARDANDO_PAGAMENTO,
                     self._agora)
        self._conn.execute(
            "UPDATE leads SET cobranca_enviada_em = ? WHERE id = ?",
            (self._agora.isoformat(), self._lead),
        )
        self._conn.commit()
        eventos.registrar(self._conn, "sistema",
                          "Venda fechada — boleto com a equipe",
                          self._agora, self._lead)
        self.fechou = True
        return ("venda registrada: a equipe vai gerar o boleto e enviar "
                "aqui na conversa em instantes")

    def _escalar(self, motivo: str) -> str:
        transicionar(self._conn, self._lead, Status.ESCALADO, self._agora)
        eventos.registrar(self._conn, "alerta",
                          f"Escalado para humano: {motivo}",
                          self._agora, self._lead)
        self.escalou = True
        return "escalado"
