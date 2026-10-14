"""Testes de persistência e da guarda de sanidade.

Tudo roda offline: as páginas vêm de `tests/fixtures/` e o banco é o fixture
`engine` (SQLite temporário por padrão; Postgres quando `TEST_DATABASE_URL`
está definida — ver `tests/conftest.py`).

A antiga cobertura do lock em arquivo foi removida junto com o próprio lock:
no GitHub Actions a exclusão mútua é o `concurrency` do workflow (AGENTS.md §7.3).
"""

from __future__ import annotations

import gzip
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrapers import config, models, persistencia
from scrapers import extractors as ex

FIXTURES = Path(__file__).parent / "fixtures"
PAGINAS = [f"listagem_p{n}.html.gz" for n in range(1, 8)]
MOMENTO = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)


def ler(nome: str) -> str:
    return gzip.decompress((FIXTURES / nome).read_bytes()).decode("utf-8")


def _utc(valor: datetime) -> datetime:
    """Normaliza para UTC: o SQLite devolve naive, o Postgres devolve aware."""
    return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


@pytest.fixture
def cards() -> dict[str, dict]:
    """Os 51 produtos reais, extraídos das páginas salvas."""
    todos: dict[str, dict] = {}
    for pagina in PAGINAS:
        for card in ex.extrair_cards(ler(pagina)):
            todos.setdefault(card["url"], card)
    return todos


def contar(session: Session, modelo) -> int:
    return session.scalar(select(func.count()).select_from(modelo)) or 0


# --------------------------------------------------------------------------
# persistir
# --------------------------------------------------------------------------

def test_primeira_gravacao(session, cards):
    resultado = persistencia.persistir(session, cards, MOMENTO)
    session.commit()

    assert resultado == {"novos": 51, "atualizados": 0, "inativos": 0}
    assert contar(session, models.Produto) == 51
    assert contar(session, models.Preco) == 51

    produto = session.scalar(select(models.Produto).limit(1))
    assert produto.ativo is True
    assert _utc(produto.ultima_visao) == MOMENTO
    assert produto.peso_g in config.PESOS_CONHECIDOS


def test_segunda_gravacao_nao_duplica_produto(session, cards):
    persistencia.persistir(session, cards, MOMENTO)
    session.commit()

    depois = MOMENTO + timedelta(hours=12)
    resultado = persistencia.persistir(session, cards, depois)
    session.commit()

    assert resultado == {"novos": 0, "atualizados": 51, "inativos": 0}
    assert contar(session, models.Produto) == 51, "URL é chave única"
    assert contar(session, models.Preco) == 102, "um snapshot por produto por coleta"


def test_produto_ausente_vira_inativo_e_mantem_historico(session, cards):
    persistencia.persistir(session, cards, MOMENTO)
    session.commit()

    faltando = next(iter(cards))
    restantes = {u: c for u, c in cards.items() if u != faltando}
    resultado = persistencia.persistir(session, restantes, MOMENTO + timedelta(hours=12))
    session.commit()

    assert resultado["inativos"] == 1
    ausente = session.scalar(select(models.Produto).where(models.Produto.url == faltando))
    assert ausente.ativo is False
    assert ausente.precos, "o histórico do produto que saiu da listagem é preservado"

    # e se o produto voltar, ele é reativado
    persistencia.persistir(session, cards, MOMENTO + timedelta(hours=24))
    session.commit()
    session.refresh(ausente)
    assert ausente.ativo is True


def test_promocao_gravada(session, cards):
    persistencia.persistir(session, cards, MOMENTO)
    session.commit()

    promos = session.scalars(select(models.Preco).where(models.Preco.em_promocao.is_(True))).all()
    assert len(promos) == 2
    for preco in promos:
        assert preco.preco_original > preco.preco


# --------------------------------------------------------------------------
# guarda de sanidade
# --------------------------------------------------------------------------

def test_guarda_aborta_com_poucos_produtos(session, cards):
    poucos = dict(list(cards.items())[:3])
    with pytest.raises(persistencia.GuardaViolada, match="produtos válidos"):
        persistencia.aplicar_guarda(poucos, sem_preco=[], produtos_vistos=3, session=session)


def test_guarda_aborta_em_queda_brusca(session, cards):
    persistencia.persistir(session, cards, MOMENTO)
    session.commit()  # 51 produtos ativos

    quarta_parte = dict(list(cards.items())[:12])  # 12/51 = queda de 76%
    with pytest.raises(persistencia.GuardaViolada, match="queda"):
        persistencia.aplicar_guarda(quarta_parte, sem_preco=[], produtos_vistos=12, session=session)


def test_guarda_aceita_variacao_pequena(session, cards):
    persistencia.persistir(session, cards, MOMENTO)
    session.commit()

    quase_todos = dict(list(cards.items())[:45])  # queda de 12%
    persistencia.aplicar_guarda(quase_todos, sem_preco=[], produtos_vistos=45, session=session)


def test_guarda_aborta_quando_muitos_cards_sem_preco(session, cards):
    with pytest.raises(persistencia.GuardaViolada, match="sem preço"):
        persistencia.aplicar_guarda(
            dict(list(cards.items())[:10]), sem_preco=["a", "b"], produtos_vistos=12, session=session
        )


def test_separar_validos(cards):
    com_problema = dict(cards)
    url_quebrada = next(iter(com_problema))
    com_problema[url_quebrada] = {**com_problema[url_quebrada], "preco": None}

    validos, sem_preco = persistencia.separar_validos(com_problema)
    assert len(validos) == 50
    assert len(sem_preco) == 1


# --------------------------------------------------------------------------
# auditoria
# --------------------------------------------------------------------------

def test_registrar_coleta(session):
    persistencia.registrar_coleta(
        session, iniciada_em=MOMENTO, status="ok", paginas=8, requisicoes=9,
        vistos=51, gravados=51,
    )
    coleta = session.scalar(select(models.Coleta))
    assert coleta.status == "ok"
    assert coleta.itens_gravados == 51
    assert coleta.finalizada_em is not None
