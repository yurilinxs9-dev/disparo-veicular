from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class MensagemNormalizada:
    tipo: str
    texto: str
    telefone: str
    wa_message_id: str
    imagem_b64: str | None = None
    media_type: str | None = None


def _telefone(jid: str) -> str:
    return jid.split("@", 1)[0].split(":", 1)[0]


def normalizar(payload: dict,
               transcritor: Callable[[bytes], str]) -> MensagemNormalizada | None:
    dados = payload.get("data") or {}
    chave = dados.get("key") or {}
    if chave.get("fromMe"):
        return None

    telefone = _telefone(chave.get("remoteJid", ""))
    wa_id = chave.get("id", "")
    msg = dados.get("message") or {}
    comum = {"telefone": telefone, "wa_message_id": wa_id}

    if "conversation" in msg:
        return MensagemNormalizada("texto", msg["conversation"], **comum)

    if "extendedTextMessage" in msg:
        texto = msg["extendedTextMessage"].get("text", "")
        return MensagemNormalizada("texto", texto, **comum)

    if "audioMessage" in msg:
        try:
            bruto = base64.b64decode(msg.get("base64", ""))
            texto = transcritor(bruto).strip()
        except Exception:
            texto = ""
        if not texto:
            texto = "[áudio que não consegui ouvir]"
        return MensagemNormalizada("audio", texto, **comum)

    if "imageMessage" in msg:
        return MensagemNormalizada(
            "imagem",
            "[o lead enviou uma foto]",
            imagem_b64=msg.get("base64"),
            media_type=msg["imageMessage"].get("mimetype", "image/jpeg"),
            **comum,
        )

    if "stickerMessage" in msg:
        return MensagemNormalizada("figurinha", "[o lead enviou uma figurinha]", **comum)

    if "videoMessage" in msg:
        return MensagemNormalizada(
            "video", "[o lead enviou um vídeo, que não consigo assistir]", **comum
        )

    if "locationMessage" in msg:
        return MensagemNormalizada("localizacao", "[o lead enviou uma localização]", **comum)

    if "contactMessage" in msg:
        return MensagemNormalizada("contato", "[o lead enviou um contato]", **comum)

    return MensagemNormalizada("outro", "[o lead enviou algo que não consigo ler]", **comum)


def transcritor_whisper(modelo: str = "small") -> Callable[[bytes], str]:
    """Devolve um transcritor que roda faster-whisper local, em português."""
    from faster_whisper import WhisperModel

    whisper = WhisperModel(modelo, device="cpu", compute_type="int8")

    def transcrever(audio: bytes) -> str:
        segmentos, _ = whisper.transcribe(io.BytesIO(audio), language="pt")
        return " ".join(s.text for s in segmentos).strip()

    return transcrever
