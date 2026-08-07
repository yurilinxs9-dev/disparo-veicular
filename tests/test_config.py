import pytest

from disparo.config import carregar_config


def _env_completo(**extras):
    base = {
        "DISPARO_DB": "x.db", "ANTHROPIC_API_KEY": "k",
        "EVOLUTION_BASE_URL": "http://e", "EVOLUTION_API_KEY": "k",
        "EVOLUTION_INSTANCE": "i", "VENDEDORA_TELEFONE": "5511900000000",
        "PAINEL_SENHA": "s",
    }
    base.update(extras)
    return base


def test_powercrm_opcional_com_defaults():
    from disparo.config import carregar_config
    cfg = carregar_config(_env_completo())
    assert cfg.powercrm_base_url == ""
    assert cfg.equipe_telefone == "5511900000000"
    assert cfg.modelo_triagem == "claude-haiku-4-5"
    assert cfg.modelo_fechamento == "claude-sonnet-5"


def test_powercrm_configurado():
    from disparo.config import carregar_config
    cfg = carregar_config(_env_completo(
        POWERCRM_BASE_URL="https://api.powercrm.com.br/",
        POWERCRM_TOKEN="t1", POWERCRM_WEBHOOK_TOKEN="t2",
        EQUIPE_TELEFONE="5537999990000",
    ))
    assert cfg.powercrm_base_url == "https://api.powercrm.com.br"
    assert cfg.powercrm_token == "t1"
    assert cfg.powercrm_webhook_token == "t2"
    assert cfg.equipe_telefone == "5537999990000"


def test_powercrm_parcial_falha_rapido():
    with pytest.raises(RuntimeError, match="POWERCRM_TOKEN"):
        carregar_config(_env_completo(
            POWERCRM_BASE_URL="https://api.powercrm.com.br/",
        ))


def test_powercrm_parcial_com_so_um_token_falha_rapido():
    with pytest.raises(RuntimeError, match="POWERCRM_WEBHOOK_TOKEN"):
        carregar_config(_env_completo(
            POWERCRM_BASE_URL="https://api.powercrm.com.br/",
            POWERCRM_TOKEN="t1",
        ))
