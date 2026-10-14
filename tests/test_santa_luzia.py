"""Teste de integração do entry point, offline (sem rede e sem Postgres).

`coletar` é substituído por um stub que devolve os cards das páginas reais de
`tests/fixtures/`, então exercitamos o caminho completo de `executar`:
guarda de sanidade -> gravação -> auditoria em `coletas`.
"""

from __future__ import annotations

import gzip
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapers import extractors as ex, models, santa_luzia

FIXTURES = Path(__file__).parent / "fixtures"


def _cards() -> dict[str, dict]:
    todos: dict[str, dict] = {}
    for n in range(1, 8):
        html = gzip.decompress((FIXTURES / f"listagem_p{n}.html.gz").read_bytes()).decode("utf-8")
        for card in ex.extrair_cards(html):
            todos.setdefault(card["url"], card)
    return todos


def _stub(produtos: dict[str, dict]):
    def coletar(paginas_max: int = 0):
        return dict(produtos), {"paginas": 8, "requisicoes": 9, "delay": 2.0}

    return coletar


def test_executar_grava_e_audita(tmp_path, monkeypatch):
    monkeypatch.setattr(santa_luzia, "coletar", _stub(_cards()))
    url = f"sqlite:///{tmp_path / 'integracao.db'}"

    assert santa_luzia.executar(url=url) == santa_luzia.SAIDA_OK

    engine = models.criar_engine(url)
    with Session(engine) as sessao:
        assert sessao.scalar(select(models.Coleta)).status == "ok"
        assert len(sessao.scalars(select(models.Produto)).all()) == 51
        assert len(sessao.scalars(select(models.Preco)).all()) == 51


def test_executar_dry_run_nao_grava(tmp_path, monkeypatch):
    monkeypatch.setattr(santa_luzia, "coletar", _stub(_cards()))
    url = f"sqlite:///{tmp_path / 'dry.db'}"

    assert santa_luzia.executar(url=url, dry_run=True) == santa_luzia.SAIDA_OK

    engine = models.criar_engine(url)
    with Session(engine) as sessao:
        assert sessao.scalar(select(models.Coleta)) is None


def test_executar_aborta_quando_guarda_dispara(tmp_path, monkeypatch):
    poucos = dict(list(_cards().items())[:3])  # abaixo de GUARDA_MIN_PRODUTOS
    monkeypatch.setattr(santa_luzia, "coletar", _stub(poucos))
    url = f"sqlite:///{tmp_path / 'guarda.db'}"

    assert santa_luzia.executar(url=url) == santa_luzia.SAIDA_ABORTADA

    engine = models.criar_engine(url)
    with Session(engine) as sessao:
        coleta = sessao.scalar(select(models.Coleta))
        assert coleta.status == "abortada"
        assert sessao.scalar(select(models.Produto)) is None
