from __future__ import annotations

import threading
from typing import Callable


class FilaPorLead:
    """Debounce e trava de processamento por lead. Thread-safe.

    O debounce não usa relógio: cada chegada incrementa um sequencial, e a
    rodada que acorda do sono só segue se ainda for a chegada mais recente.
    Assim, mensagem nova durante a janela "reinicia o relógio" naturalmente —
    a rodada dela dorme a janela inteira a partir da chegada dela.
    """

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._seq: dict[int, int] = {}
        self._ocupado: set[int] = set()
        self._pendente: set[int] = set()
        self._midia: dict[int, tuple[str, str]] = {}
        self._respondido: dict[int, int] = {}

    def chegou(self, lead_id: int, imagem_b64: str | None = None,
               media_type: str | None = None) -> int:
        with self._trava:
            self._seq[lead_id] = self._seq.get(lead_id, 0) + 1
            if imagem_b64:
                self._midia[lead_id] = (imagem_b64, media_type or "image/jpeg")
            return self._seq[lead_id]

    def aguardar(self, lead_id: int, meu_seq: int, janela: float,
                 dormir: Callable[[float], None]) -> bool:
        dormir(janela)
        with self._trava:
            return self._seq.get(lead_id) == meu_seq

    def assumir(self, lead_id: int) -> bool:
        with self._trava:
            if lead_id in self._ocupado:
                self._pendente.add(lead_id)
                return False
            self._ocupado.add(lead_id)
            return True

    def liberar(self, lead_id: int) -> bool:
        with self._trava:
            self._ocupado.discard(lead_id)
            if lead_id in self._pendente:
                self._pendente.discard(lead_id)
                return True
            return False

    def mudou(self, lead_id: int, seq: int) -> bool:
        with self._trava:
            return self._seq.get(lead_id, 0) != seq

    def seq_atual(self, lead_id: int) -> int:
        with self._trava:
            return self._seq.get(lead_id, 0)

    def cobriu(self, lead_id: int, seq: int) -> None:
        """Registra até onde uma resposta efetivamente enviada cobriu o
        sequencial do lead. Fonte única de verdade para o guard de
        histórico terminando em 'saida' — evita que esse valor precise
        viajar por parâmetro/retorno entre rodadas (ver `_responder`)."""
        with self._trava:
            self._respondido[lead_id] = seq

    def seq_respondido(self, lead_id: int) -> int:
        with self._trava:
            return self._respondido.get(lead_id, 0)

    def tirar_midia(self, lead_id: int) -> tuple[str, str] | None:
        with self._trava:
            return self._midia.pop(lead_id, None)
