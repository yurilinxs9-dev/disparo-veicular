# tests/test_painel.py
import random
from datetime import date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from disparo.app import criar_app
from disparo.cota import definir_inicio

AUTH = ("operador", "segredo")


def _estado(conn):
    return SimpleNamespace(
        conn=conn, evo=None, claude=None, rng=random.Random(1),
        transcritor=lambda b: "", dormir=lambda s: None,
        cfg=SimpleNamespace(vendedora_telefone="5511900000000",
                            painel_senha="segredo"),
    )


def test_exige_autenticacao(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    assert cliente.get("/api/estado").status_code == 401


def test_senha_errada_e_recusada(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    assert cliente.get("/api/estado", auth=("operador", "errada")).status_code == 401


def test_estado_traz_cota_e_disjuntor(conn):
    definir_inicio(conn, date(2026, 8, 3))
    cliente = TestClient(criar_app(_estado(conn)))
    dados = cliente.get("/api/estado", auth=AUTH).json()
    assert dados["limite"] in (10, 20, 30)
    assert dados["pausado"] is False
    assert "disjuntor" in dados


def test_lista_leads_com_filtro_e_busca(conn, lead):
    cliente = TestClient(criar_app(_estado(conn)))
    todos = cliente.get("/api/leads", auth=AUTH).json()
    assert len(todos) == 1
    assert cliente.get("/api/leads?status=quente", auth=AUTH).json() == []
    assert len(cliente.get("/api/leads?busca=Joao", auth=AUTH).json()) == 1
    assert cliente.get("/api/leads?busca=Zulmira", auth=AUTH).json() == []


def test_conversa_de_um_lead(conn, lead):
    conn.execute(
        "INSERT INTO mensagens (lead_id, direcao, texto, wa_message_id, criado_em) "
        "VALUES (?, 'saida', 'Oii Joao, tudo bem?', 'WA1', ?)",
        (lead, datetime(2026, 8, 4, 9).isoformat()),
    )
    conn.commit()
    cliente = TestClient(criar_app(_estado(conn)))
    dados = cliente.get(f"/api/leads/{lead}", auth=AUTH).json()
    assert dados["lead"]["nome"] == "Joao"
    assert len(dados["mensagens"]) == 1


def test_pausar_e_retomar_pela_api(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    cliente.post("/api/pausar", json={"pausar": True}, auth=AUTH)
    assert cliente.get("/api/estado", auth=AUTH).json()["pausado"] is True
    cliente.post("/api/pausar", json={"pausar": False}, auth=AUTH)
    assert cliente.get("/api/estado", auth=AUTH).json()["pausado"] is False


def test_importar_csv_pela_api(conn):
    cliente = TestClient(criar_app(_estado(conn)))
    csv = b"nome,telefone,veiculo\nAna,11955551111,Kwid 2020\n"
    resposta = cliente.post(
        "/api/importar", files={"arquivo": ("lista.csv", csv, "text/csv")}, auth=AUTH
    )
    assert resposta.json()["importados"] == 1
