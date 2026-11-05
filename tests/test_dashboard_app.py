"""Testes de renderização do painel (AppTest), separados dos testes de dados.

Aqui só entram regressões que **só aparecem na tela**: um `st.slider` com limites
iguais derrubava o script inteiro e as abas sumiam sem mensagem de erro no banco.
"""

from __future__ import annotations

import gzip
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from dashboard import dados
from scrapers import persistencia
from scrapers import extractors as ex

APP = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def cards() -> dict[str, dict]:
    todos: dict[str, dict] = {}
    for pagina in [f"listagem_p{n}.html.gz" for n in range(1, 8)]:
        html = gzip.decompress((FIXTURES / pagina).read_bytes()).decode("utf-8")
        for card in ex.extrair_cards(html):
            todos.setdefault(card["url"], card)
    return todos


@pytest.fixture
def banco(engine, cards):
    """Duas coletas, para a variação existir (senão a tabela fica com '—')."""
    primeira = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
    segunda = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
    with Session(engine) as sessao:
        persistencia.persistir(sessao, cards, primeira)
        sessao.commit()
        urls = list(cards)
        alterados = {**cards, urls[0]: {**cards[urls[0]], "preco": round(cards[urls[0]]["preco"] * 0.8, 2)}}
        persistencia.persistir(sessao, alterados, segunda)
        persistencia.registrar_coleta(sessao, iniciada_em=segunda, status="ok",
                                      paginas=8, requisicoes=9, vistos=51, gravados=51)
    return engine


def _historico_fake(dias: int = 5) -> pd.DataFrame:
    """Histórico mínimo para a aba de evolução renderizar sem tocar no banco."""
    base = pd.Timestamp("2026-10-13 20:00", tz="UTC")
    datas = [base - pd.Timedelta(days=i) for i in range(dias)]
    return pd.DataFrame({
        "data_coleta": datas,
        "preco": [80.0 + i for i in range(dias)],
        "preco_original": [None] * dias,
        "em_promocao": [False] * dias,
        "data_local": [d.tz_convert("America/Sao_Paulo") for d in datas],
    })


def _rodar_app(monkeypatch, produtos: pd.DataFrame, coletas: pd.DataFrame) -> AppTest:
    """Roda o app com dados injetados (sem tocar no Neon)."""
    historico = _historico_fake()
    monkeypatch.setattr(dados, "carregar_produtos", lambda *a, **k: produtos)
    monkeypatch.setattr(dados, "carregar_coletas", lambda *a, **k: coletas)
    monkeypatch.setattr(dados, "carregar_historico", lambda *a, **k: historico)
    import streamlit as st

    st.cache_data.clear()  # o app embrulha as duas funções em @st.cache_data
    return AppTest.from_file(APP, default_timeout=60).run()


def test_slider_com_limite_unico_nao_derruba_a_pagina(banco, monkeypatch):
    """A-3: com todos os preços iguais, `min_value == max_value` estourava o slider
    e a página inteira sumia (só sobrava o traceback)."""
    produtos = dados.carregar_produtos(banco).copy()
    produtos["preco"] = 82.0
    produtos["preco_por_kg"] = 328.0
    coletas = dados.carregar_coletas(engine=banco)

    app = _rodar_app(monkeypatch, produtos, coletas)

    assert not app.exception, [e.value for e in app.exception]
    assert app.sidebar.caption, "a faixa única deveria virar caption, não slider"
    assert any("82,00" in c.value for c in app.sidebar.caption)
    assert len(app.tabs) == 3, "as abas precisam continuar de pé"


def test_metricas_mostram_queda_e_alta(banco, monkeypatch):
    """B-3: 'maior queda' e 'maior alta' convivem, e nenhuma vira '0,00%' falsa."""
    produtos = dados.carregar_produtos(banco)
    coletas = dados.carregar_coletas(engine=banco)

    app = _rodar_app(monkeypatch, produtos, coletas)

    assert not app.exception, [e.value for e in app.exception]
    rotulos = {m.label: m.value for m in app.metric}
    assert rotulos["Maior queda"].startswith("▼")
    assert rotulos.get("Maior alta", "—") == "—" or rotulos["Maior alta"].startswith("▲")
    assert rotulos["Maior queda"] != "0,00%"
