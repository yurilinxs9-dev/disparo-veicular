import base64

from disparo.midia import normalizar


def _envelope(interno: dict, id_: str = "WA1") -> dict:
    return {"data": {"key": {"id": id_, "remoteJid": "5511988884444@s.whatsapp.net",
                             "fromMe": False},
                     "message": interno}}


def test_texto_simples():
    m = normalizar(_envelope({"conversation": "tudo bem e vc"}), lambda b: "")
    assert m.tipo == "texto"
    assert m.texto == "tudo bem e vc"
    assert m.telefone == "5511988884444"
    assert m.wa_message_id == "WA1"


def test_texto_estendido():
    m = normalizar(
        _envelope({"extendedTextMessage": {"text": "passo sim"}}), lambda b: ""
    )
    assert m.texto == "passo sim"


def test_audio_e_transcrito():
    payload = _envelope({"audioMessage": {"mimetype": "audio/ogg"},
                         "base64": base64.b64encode(b"bytes").decode()})
    m = normalizar(payload, lambda b: "passo sim toda semana")
    assert m.tipo == "audio"
    assert m.texto == "passo sim toda semana"


def test_imagem_vira_bloco_de_imagem():
    payload = _envelope({"imageMessage": {"mimetype": "image/jpeg"},
                         "base64": base64.b64encode(b"jpg").decode()})
    m = normalizar(payload, lambda b: "")
    assert m.tipo == "imagem"
    assert m.media_type == "image/jpeg"
    assert m.imagem_b64 is not None


def test_figurinha_vira_reacao():
    m = normalizar(_envelope({"stickerMessage": {}}), lambda b: "")
    assert m.tipo == "figurinha"
    assert m.texto == "[o lead enviou uma figurinha]"


def test_video_pede_texto():
    m = normalizar(_envelope({"videoMessage": {}}), lambda b: "")
    assert m.tipo == "video"
    assert "vídeo" in m.texto


def test_mensagem_propria_e_ignorada():
    payload = _envelope({"conversation": "oi"})
    payload["data"]["key"]["fromMe"] = True
    assert normalizar(payload, lambda b: "") is None


def test_transcricao_que_falha_nao_derruba():
    def quebrado(_: bytes) -> str:
        raise RuntimeError("whisper caiu")

    payload = _envelope({"audioMessage": {"mimetype": "audio/ogg"},
                         "base64": base64.b64encode(b"x").decode()})
    m = normalizar(payload, quebrado)
    assert m.tipo == "audio"
    assert "não consegui ouvir" in m.texto


def test_transcricao_com_sucesso_nao_sinaliza_falha():
    payload = _envelope({"audioMessage": {"mimetype": "audio/ogg"},
                         "base64": base64.b64encode(b"bytes").decode()})
    m = normalizar(payload, lambda b: "passo sim toda semana")
    assert m.transcricao_falhou is False


def test_transcritor_que_quebra_sinaliza_falha():
    def quebrado(_: bytes) -> str:
        raise RuntimeError("whisper caiu")

    payload = _envelope({"audioMessage": {"mimetype": "audio/ogg"},
                         "base64": base64.b64encode(b"x").decode()})
    m = normalizar(payload, quebrado)
    assert m.transcricao_falhou is True
    assert m.tipo == "audio"
    assert "não consegui ouvir" in m.texto


def test_base64_malformado_sinaliza_falha_sem_propagar():
    payload = _envelope({"audioMessage": {"mimetype": "audio/ogg"},
                         "base64": "não é base64!!"})
    m = normalizar(payload, lambda b: "não deveria chegar aqui")
    assert m.transcricao_falhou is True


def test_mensagem_de_texto_nao_sinaliza_falha():
    m = normalizar(_envelope({"conversation": "tudo bem e vc"}), lambda b: "")
    assert m.transcricao_falhou is False
