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


def test_colunas_da_etapa_2_existem(conn):
    colunas = {l["name"] for l in conn.execute("PRAGMA table_info(leads)")}
    assert {"placa", "cotacao_id", "plano", "mensalidade", "adesao",
            "cobranca_id", "boleto_url", "cobranca_enviada_em",
            "lembrete_em"} <= colunas


def test_garantir_colunas_e_idempotente(conn):
    from disparo.db import garantir_colunas
    garantir_colunas(conn)
    garantir_colunas(conn)  # segunda chamada não pode explodir
