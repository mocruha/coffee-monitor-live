"""Fixtures comuns dos testes.

Por padrão os testes rodam em **SQLite** (arquivo temporário), o que os mantém
herméticos: não precisam de rede nem de um Postgres instalado.

Se `TEST_DATABASE_URL` estiver definida, a MESMA suíte roda contra um Postgres
real — é assim que o CI (GitHub Actions) valida o dialeto do Neon usando um
service container. Ver `.github/workflows/testes.yml`.
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from scrapers import base, models
from scrapers import extractors as ex

FIXTURES = Path(__file__).parent / "fixtures"

# Pesos reais da categoria — documentação de domínio, não configuração de runtime:
# nenhum código do scraper ou do painel usa isso. Vivem aqui para os testes
# afirmarem que o parser só produz pesos que a loja realmente vende.
PESOS_CONHECIDOS = (150, 250, 500, 1000)


def ler_fixture(nome: str) -> str:
    return gzip.decompress((FIXTURES / nome).read_bytes()).decode("utf-8")


@pytest.fixture(scope="session")
def cards() -> dict[str, dict]:
    """Os 51 produtos reais das 7 páginas salvas (chave: URL).

    Fixture de sessão: o parsing das páginas é idêntico em todos os testes e
    acontecia cinco vezes, uma cópia por arquivo de teste.
    """
    todos: dict[str, dict] = {}
    for n in range(1, 8):
        for card in ex.extrair_cards(ler_fixture(f"listagem_p{n}.html.gz")):
            todos.setdefault(card["url"], card)
    return todos


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
