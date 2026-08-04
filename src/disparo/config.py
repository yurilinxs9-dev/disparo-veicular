from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    db: Path
    anthropic_api_key: str
    evolution_base_url: str
    evolution_api_key: str
    evolution_instance: str
    vendedora_telefone: str
    painel_senha: str
    whisper_modelo: str


def carregar_config(env: Mapping[str, str] | None = None) -> Config:
    e = env if env is not None else os.environ
    faltando = [
        chave for chave in (
            "DISPARO_DB", "ANTHROPIC_API_KEY", "EVOLUTION_BASE_URL",
            "EVOLUTION_API_KEY", "EVOLUTION_INSTANCE",
            "VENDEDORA_TELEFONE", "PAINEL_SENHA",
        )
        if not e.get(chave)
    ]
    if faltando:
        raise RuntimeError("variáveis de ambiente ausentes: " + ", ".join(faltando))
    return Config(
        db=Path(e["DISPARO_DB"]),
        anthropic_api_key=e["ANTHROPIC_API_KEY"],
        evolution_base_url=e["EVOLUTION_BASE_URL"].rstrip("/"),
        evolution_api_key=e["EVOLUTION_API_KEY"],
        evolution_instance=e["EVOLUTION_INSTANCE"],
        vendedora_telefone=e["VENDEDORA_TELEFONE"],
        painel_senha=e["PAINEL_SENHA"],
        whisper_modelo=e.get("WHISPER_MODELO", "small"),
    )
