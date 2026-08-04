import sqlite3
from datetime import datetime

import pytest

from disparo.db import conectar, criar_schema


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = conectar(tmp_path / "teste.db")
    criar_schema(c)
    yield c
    c.close()


@pytest.fixture
def lead(conn) -> int:
    conn.execute(
        "INSERT INTO leads (nome, telefone_e164, veiculo, criado_em) "
        "VALUES (?, ?, ?, ?)",
        ("Joao", "5511988884444", "Onix 2019", datetime(2026, 8, 4, 9).isoformat()),
    )
    conn.commit()
    return conn.execute("SELECT id FROM leads").fetchone()["id"]
