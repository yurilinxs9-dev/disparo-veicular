from disparo.config import carregar_config
from disparo.db import conectar, criar_schema


def test_config_le_variaveis_de_ambiente():
    cfg = carregar_config({
        "DISPARO_DB": "/tmp/x.db",
        "ANTHROPIC_API_KEY": "sk-teste",
        "EVOLUTION_BASE_URL": "http://evo:8080",
        "EVOLUTION_API_KEY": "evo-key",
        "EVOLUTION_INSTANCE": "portosul-01",
        "VENDEDORA_TELEFONE": "5511999999999",
        "PAINEL_SENHA": "segredo",
    })
    assert cfg.evolution_instance == "portosul-01"
    assert cfg.whisper_modelo == "small"


def test_schema_cria_todas_as_tabelas(tmp_path):
    conn = conectar(tmp_path / "t.db")
    criar_schema(conn)
    nomes = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"leads", "mensagens", "blocklist", "envios_diarios",
            "eventos", "config"} <= nomes


def test_criar_schema_e_idempotente(tmp_path):
    conn = conectar(tmp_path / "t.db")
    criar_schema(conn)
    criar_schema(conn)  # não pode explodir
