"""Fixtures comuns dos testes.

Por padrão os testes rodam em **SQLite** (arquivo temporário), o que os mantém
herméticos: não precisam de rede nem de um Postgres instalado.

Se `TEST_DATABASE_URL` estiver definida, a MESMA suíte roda contra um Postgres
real — é assim que o CI (GitHub Actions) valida o dialeto do Neon usando um
service container. Ver `.github/workflows/testes.yml`.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.orm import Session

from scrapers import base, models


@pytest.fixture
def engine(tmp_path):
    """Engine limpo por teste (SQLite temporário ou Postgres do CI)."""
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        eng = base.criar_engine(url)
        models.Base.metadata.drop_all(eng)
        models.criar_schema(eng)
    else:
        eng = base.criar_engine(f"sqlite:///{tmp_path / 'teste.db'}")
        models.criar_schema(eng)
    yield eng
    if url:
        models.Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sessao:
        yield sessao
