"""Testes da camada de dados do dashboard.

O ponto crítico aqui é a variação: com o `LAG` calculado depois do filtro da
coleta mais recente, a coluna viria `NULL` em todos os produtos — sem erro.
Estes testes existem para que essa regressão nunca passe despercebida.

Também verificamos que o dashboard não escreve no banco (política de somente
leitura) e que as chaves lidas pelo app existem no resumo.
"""

from __future__ import annotations

import gzip
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from dashboard import dados
from scrapers import models, persistencia
from scrapers import extractors as ex

FIXTURES = Path(__file__).parent / "fixtures"
PRIMEIRA = datetime(2026, 10, 13, 8, 0, 0, tzinfo=timezone.utc)
SEGUNDA = datetime(2026, 10, 13, 20, 0, 0, tzinfo=timezone.utc)


def ler(nome: str) -> str:
    return gzip.decompress((FIXTURES / nome).read_bytes()).decode("utf-8")


@pytest.fixture
def cards() -> dict[str, dict]:
    todos: dict[str, dict] = {}
    for pagina in [f"listagem_p{n}.html.gz" for n in range(1, 8)]:
        for card in ex.extrair_cards(ler(pagina)):
            todos.setdefault(card["url"], card)
    return todos


@pytest.fixture
def banco(engine, cards):
    """Banco com duas coletas: um produto cai 20%, outro sobe, o resto fica igual."""
    with Session(engine) as sessao:
        persistencia.persistir(sessao, cards, PRIMEIRA)
        sessao.commit()

    urls = list(cards)
    alterados = dict(cards)
    alterados[urls[0]] = {**cards[urls[0]], "preco": round(cards[urls[0]]["preco"] * 0.8, 2)}
    alterados[urls[1]] = {**cards[urls[1]], "preco": round(cards[urls[1]]["preco"] + 5, 2)}

    with Session(engine) as sessao:
        persistencia.persistir(sessao, alterados, SEGUNDA)
        persistencia.registrar_coleta(sessao, iniciada_em=SEGUNDA, status="ok", paginas=8,
                                      requisicoes=9, vistos=51, gravados=51)
    return engine


def test_carregar_produtos_traz_variacao(banco):
    df = dados.carregar_produtos(banco)
    assert len(df) == 51
    # a armadilha: sem a CTE, TODAS as variações viriam nulas
    assert df["variacao_pct"].notna().sum() == 51, "variação veio nula — o LAG foi filtrado antes de calcular"
    assert df["preco_anterior"].notna().all()


def test_variacao_tem_os_valores_certos(banco):
    df = dados.carregar_produtos(banco)
    quedas = df[df["variacao_pct"] < 0]
    altas = df[df["variacao_pct"] > 0]
    assert len(quedas) == 1
    assert len(altas) == 1
    assert quedas["variacao_pct"].iloc[0] == pytest.approx(-20.0, abs=0.1)
    assert quedas["variacao_abs"].iloc[0] < 0


def test_preco_por_kg(banco):
    df = dados.carregar_produtos(banco)
    linha = df[df["peso_g"] == 1000].iloc[0]
    assert linha["preco_por_kg"] == pytest.approx(linha["preco"], abs=0.01)
    linha250 = df[df["peso_g"] == 250].iloc[0]
    assert linha250["preco_por_kg"] == pytest.approx(linha250["preco"] * 4, abs=0.01)


def test_data_vem_convertida_para_brasilia(banco):
    df = dados.carregar_produtos(banco)
    atualizado = df["atualizado_em"].iloc[0]
    assert str(atualizado.tzinfo) == "America/Sao_Paulo"
    assert atualizado.hour == SEGUNDA.hour - 3  # 20h UTC -> 17h BRT


def test_dashboard_nao_escreve():
    """Política de somente leitura: o módulo de dados não pode emitir escrita."""
    fonte = (Path(__file__).resolve().parent.parent / "dashboard" / "dados.py").read_text(
        encoding="utf-8"
    )
    proibidos = ("session.add", ".commit(", ".flush(", "INSERT INTO", "UPDATE ", "DELETE FROM",
                 "DROP ", "TRUNCATE")
    for termo in proibidos:
        assert termo not in fonte, f"o dashboard não deve escrever no banco: {termo!r}"


def test_resumo(banco):
    df = dados.carregar_produtos(banco)
    coletas = dados.carregar_coletas(engine=banco)
    resumo = dados.resumo(df, coletas)
    assert resumo["ativos"] == 51
    assert resumo["promocoes"] == 2
    assert resumo["ultimo_status"] == "ok"
    assert resumo["maior_queda"][1] < 0
    assert resumo["sem_variacao"] == 0
    assert resumo["preco_por_kg_mediano"] > 0


def test_historico_de_um_produto(banco):
    df = dados.carregar_produtos(banco)
    produto = df.iloc[0]
    historico = dados.carregar_historico(int(produto["produto_id"]), banco)
    assert len(historico) == 2
    assert historico["data_local"].is_monotonic_increasing


def test_chaves_usadas_no_app_existem_no_resumo(banco):
    """O app lê `resumo["chave"]`; se uma chave mudar de nome em dados.py, o painel
    quebra em tempo de execução (foi o que aconteceu com preco_por_kg -> preco_por_kg_mediano)."""
    df = dados.carregar_produtos(banco)
    coletas = dados.carregar_coletas(engine=banco)
    resumo = dados.resumo(df, coletas)

    fonte_app = (Path(__file__).resolve().parent.parent / "dashboard" / "app.py").read_text(encoding="utf-8")
    chaves = set(re.findall(r'resumo\[\s*"([^"]+)"\s*\]', fonte_app))
    assert chaves, "nenhuma chave encontrada — o regex ou o app mudaram demais"
    faltando = chaves - set(resumo)
    assert not faltando, f"o app usa chaves que dados.resumo não produz: {sorted(faltando)}"


def test_formatar_brl():
    assert dados.formatar_brl(1234.5) == "R$ 1.234,50"
    assert dados.formatar_brl(None) == "—"
    # o `$` precisa sair escapado para o markdown do Streamlit não virar LaTeX
    assert dados.formatar_brl_md(112.0) == "R\\$ 112,00"


def test_inativo_aparece_marcado(engine, cards):
    urls = list(cards)
    with Session(engine) as sessao:
        persistencia.persistir(sessao, cards, PRIMEIRA)
        sessao.commit()
        persistencia.persistir(sessao, {u: cards[u] for u in urls[1:]}, SEGUNDA)
        sessao.commit()

    df = dados.carregar_produtos(engine)
    assert (~df["ativo"]).sum() == 1
    assert df[~df["ativo"]]["ultima_visao"].notna().all()
