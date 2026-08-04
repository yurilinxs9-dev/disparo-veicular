# tests/test_importador.py
from datetime import datetime

from disparo.blocklist import bloquear
from disparo.importador import importar_csv

AGORA = datetime(2026, 8, 4, 9, 0)

CSV = """nome,telefone,veiculo
Joao Silva,(11) 98888-4444,Onix 2019
Maria Souza,11977773333,HB20 2021
Joao Silva,11988884444,Onix 2019
Sem Telefone,abc,Gol 2015
Bloqueado,11966662222,Palio 2012
"""


def test_importa_normaliza_e_conta(conn):
    bloquear(conn, "5511966662222", "opt_out", AGORA)
    rel = importar_csv(conn, CSV, AGORA)
    assert rel.lidos == 5
    assert rel.importados == 2
    assert rel.duplicados == 1
    assert rel.invalidos == 1
    assert rel.bloqueados == 1


def test_bloqueado_nao_entra(conn):
    bloquear(conn, "5511966662222", "opt_out", AGORA)
    importar_csv(conn, CSV, AGORA)
    linha = conn.execute(
        "SELECT 1 FROM leads WHERE telefone_e164 = ?", ("5511966662222",)
    ).fetchone()
    assert linha is None


def test_reimportar_o_mesmo_arquivo_nao_duplica(conn):
    bloquear(conn, "5511966662222", "opt_out", AGORA)
    importar_csv(conn, CSV, AGORA)
    rel = importar_csv(conn, CSV, AGORA)
    assert rel.importados == 0
    total = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    assert total == 2


def test_cabecalho_com_maiuscula_e_espaco(conn):
    csv = "Nome, Telefone , Veiculo\nAna,11955551111,Kwid 2020\n"
    rel = importar_csv(conn, csv, AGORA)
    assert rel.importados == 1
