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
        "name": "gerar_cobranca",
        "description": "Gera o boleto (pagável por PIX) da adesão. Use SOMENTE "
                       "depois de o cliente aceitar explicitamente o valor.",
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
        self.falhas_powercrm = 0

    def executar(self, nome: str, entrada: dict) -> str:
        if nome == "cotar":
            return self._cotar(entrada.get("placa", ""))
        if nome == "gerar_cobranca":
            return self._gerar_cobranca()
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
            "UPDATE leads SET placa = ?, cotacao_id = ?, plano = ?, "
            "mensalidade = ?, adesao = ? WHERE id = ?",
            (placa, cot.cotacao_id, cot.plano, cot.mensalidade, cot.adesao,
             self._lead),
        )
        self._conn.commit()
        if status_de(self._conn, self._lead) == Status.EM_CONVERSA:
            transicionar(self._conn, self._lead, Status.NEGOCIANDO, self._agora)
        eventos.registrar(self._conn, "sistema",
                          f"Cotação {cot.plano}: R$ {cot.mensalidade}/mês",
                          self._agora, self._lead)
        return (f"plano {cot.plano}: mensalidade R$ {cot.mensalidade}, "
                f"adesao R$ {cot.adesao}")

    def _gerar_cobranca(self) -> str:
        lead = self._linha()
        if not lead["cotacao_id"]:
            return "erro: nenhuma cotacao feita"
        try:
            cob = self._power.gerar_cobranca(lead["cotacao_id"])
        except PowerCRMIndisponivel as erro:
            self.falhas_powercrm += 1
            eventos.registrar(self._conn, "alerta",
                              f"Power CRM falhou na cobrança: {erro}",
                              self._agora, self._lead)
            return "erro: sistema de cobranca fora do ar"
        except PowerCRMRecusa as erro:
            eventos.registrar(self._conn, "alerta",
                              f"Power CRM recusou a cobrança: {erro}",
                              self._agora, self._lead)
            return f"erro: cobranca recusada ({erro})"
        self._conn.execute(
            "UPDATE leads SET cobranca_id = ?, boleto_url = ?, "
            "cobranca_enviada_em = ? WHERE id = ?",
            (cob.cobranca_id, cob.url_boleto, self._agora.isoformat(),
             self._lead),
        )
        self._conn.commit()
        if status_de(self._conn, self._lead) == Status.NEGOCIANDO:
            transicionar(self._conn, self._lead, Status.AGUARDANDO_PAGAMENTO,
                         self._agora)
        eventos.registrar(self._conn, "sistema", "Boleto enviado",
                          self._agora, self._lead)
        return f"cobranca criada: {cob.url_boleto} | pix: {cob.pix_copia_cola}"

    def _escalar(self, motivo: str) -> str:
        transicionar(self._conn, self._lead, Status.ESCALADO, self._agora)
        eventos.registrar(self._conn, "alerta",
                          f"Escalado para humano: {motivo}",
                          self._agora, self._lead)
        self.escalou = True
        return "escalado"
