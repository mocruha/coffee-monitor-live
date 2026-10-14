"""Testes do parsing da listagem (AGENTS.md §4).

Os testes de fixture rodam OFFLINE contra o HTML real salvo em
`tests/fixtures/` (8 páginas da categoria "Café em grãos", capturadas em
13/10/2026: 7 páginas de produtos + a 8ª vazia). Eles servem como alarme de
mudança de layout: se a Santa Luzia mudar o HTML dos cards, estes testes quebram
antes de o histórico ser envenenado.

O teste de rede (`test_listagem_ao_vivo`) fica desligado por padrão; rode com
CAFE_LIVE=1 para validar contra o site de verdade.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from pathlib import Path

import pytest

from scrapers import config
from scrapers import extractors as ex

FIXTURES = Path(__file__).parent / "fixtures"
PAGINAS = [f"listagem_p{n}.html.gz" for n in range(1, 8)]


def ler_fixture(nome: str) -> str:
    return gzip.decompress((FIXTURES / nome).read_bytes()).decode("utf-8")


@pytest.fixture(scope="session")
def produtos() -> dict[str, dict]:
    """Todos os produtos extraídos das páginas reais (chave: URL)."""
    todos: dict[str, dict] = {}
    for pagina in PAGINAS:
        for card in ex.extrair_cards(ler_fixture(pagina)):
            todos.setdefault(card["url"], card)
    return todos


@pytest.fixture(scope="session")
def esperado() -> dict[str, dict]:
    return json.loads((FIXTURES / "esperado_51.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# parse_titulo: casos reais que quebraram o regex original do AGENTS.md
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "titulo, nome_esperado, peso_esperado",
    [
        # caso do AGENTS.md, que sempre funcionou
        ("Café Santa Luzia Torra Média - 250g", "Café Santa Luzia Torra Média", 250),
        # travessão unicode em vez de hífen
        ("Café Coletânea Torra Clara em Grãos Bravo – 250g", "Café Coletânea Torra Clara em Grãos Bravo", 250),
        # sem separador nenhum
        ("Café em Grãos Acaiá Guaspari 250g", "Café em Grãos Acaiá Guaspari", 250),
        # sem separador + kg
        ("Café Torrado em Grãos Luvizotto 1kg", "Café Torrado em Grãos Luvizotto", 1000),
        # número no meio do nome (o "1" do nanolote NÃO é o peso)
        ("Café Em Grãos Nanolote Orgânico Catiguá 1 Lobo do Serrote 250g",
         "Café Em Grãos Nanolote Orgânico Catiguá 1 Lobo do Serrote", 250),
        # espaço duplo antes do separador
        ("Café Rituais Grão 3 Corações  - 500g", "Café Rituais Grão 3 Corações", 500),
        # espaço duplo depois de "Café" (o parser normaliza para um espaço só)
        ("Café  Grão Intenso Fazenda Floresta - 1kg", "Café Grão Intenso Fazenda Floresta", 1000),
        # peso fora do padrão 250/500/1000
        ("Café Rapadura Silvia Magalhães Cafés 150g", "Café Rapadura Silvia Magalhães Cafés", 150),
        # nome com travessão no meio (não pode confundir o parser)
        ("Café Latitude 13 Grãos BLend Chapada Diamantina 500g",
         "Café Latitude 13 Grãos BLend Chapada Diamantina", 500),
    ],
)
def test_parse_titulo(titulo, nome_esperado, peso_esperado):
    nome, peso = ex.parse_titulo(titulo)
    assert peso == peso_esperado
    assert nome == nome_esperado


def test_parse_titulo_sem_peso():
    nome, peso = ex.parse_titulo("Café Especial da Casa")
    assert peso is None
    assert nome == "Café Especial da Casa"


def test_regex_original_do_agents_md_falhava():
    """Documenta o recon: o PADRAO_TITULO do AGENTS.md §4.1 não serve para os dados reais."""
    padrao_original = re.compile(
        r"^Café\s+(?P<nome>.+?)\s*-\s*(?P<peso>\d+)\s*(?P<unidade>g|kg)$", re.IGNORECASE
    )
    titulos = [c["titulo"] for c in _produtos_offline()]
    falhas = [t for t in titulos if not padrao_original.match(t)]
    assert len(falhas) > len(titulos) / 2, "esperado que o regex original falhasse na maioria"


# --------------------------------------------------------------------------
# parse_preco / parse_torra / parse_processo / parse_marca
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("R$ 82,00", 82.0),
        # a VTEX fatia o preço em spans: o get_text devolve espaços entre eles
        ("R$ 82 , 00", 82.0),
        ("R$ 1.234,56", 1234.56),
        ("R$ 59 , 90", 59.90),
        ("", None),
        (None, None),
        ("indisponível", None),
    ],
)
def test_parse_preco(texto, esperado):
    assert ex.parse_preco(texto) == esperado


@pytest.mark.parametrize(
    "titulo, esperado",
    [
        ("Café Coletânea Torra Clara em Grãos Bravo", "clara"),
        ("Café Em Grãos Torra Média Moderado Zaro Café", "média"),
        ("Café Grão Torra Média DuPan", "média"),
        ("Café Grão Torra Intensa DuPan", "intensa"),
        ("Café Torra Média-Clara da Casa", "média-clara"),
        ("Café Torrado em Grãos Luvizotto", None),
    ],
)
def test_parse_torra(titulo, esperado):
    assert ex.parse_torra(titulo) == esperado


@pytest.mark.parametrize(
    "titulo, esperado",
    [
        ("Café Descafeinado Torrado em Grãos Baobá", "descafeinado"),
        ("Café Natural do Cerrado", "natural"),
        ("Café Honey da Fazenda", "honey"),
        ("Café Torrado em Grãos Luvizotto", None),
    ],
)
def test_parse_processo(titulo, esperado):
    assert ex.parse_processo(titulo) == esperado


@pytest.mark.parametrize(
    "titulo, esperado",
    [
        ("Café Rituais Grão 3 Corações", "3 Corações"),
        # casamento mais longo vence
        ("Café Rapadura Silvia Magalhães Cafés", "Silvia Magalhães Cafés"),
        ("Café torrado em grãos illy", "illy"),
        ("Café em Grãos Intenso Zaro Café", "Zaro Café"),
        # sem acento no título, com acento na lista
        ("Café em Graos Baoba", "Baobá"),
        # marca desconhecida -> None (nada de chute)
        ("Café Grão Intenso Fazenda Floresta", None),
    ],
)
def test_parse_marca(titulo, esperado):
    assert ex.parse_marca(titulo) == esperado


# --------------------------------------------------------------------------
# extração da listagem
# --------------------------------------------------------------------------

def test_pagina_de_produtos():
    cards = ex.extrair_cards(ler_fixture("listagem_p1.html.gz"))
    assert len(cards) == 8
    for card in cards:
        assert card["url"].startswith("https://www.santaluzia.com.br/")
        assert card["url"].endswith("/p") or "/p" in card["url"]
        assert card["preco"] and card["preco"] > 0
        assert card["peso_g"] in config.PESOS_CONHECIDOS


def test_pagina_parcial():
    assert len(ex.extrair_cards(ler_fixture("listagem_p7.html.gz"))) == 3


def test_pagina_vazia_e_o_criterio_de_parada():
    """A 8ª página responde HTTP 200 com HTML normal, mas sem nenhum card."""
    html = ler_fixture("listagem_p8_vazia.html.gz")
    assert len(html) > 100_000  # não é uma página de erro
    assert ex.extrair_cards(html) == []


def test_conjunto_completo_de_produtos(produtos):
    assert len(produtos) == 51
    for card in produtos.values():
        assert card["nome"] and card["nome"].lower().startswith("café")
        assert card["peso_g"] in config.PESOS_CONHECIDOS
        assert 20 <= card["preco"] <= 1000
        assert card["preco_original"] is None or card["preco_original"] >= card["preco"]


def test_promocao(produtos):
    promo = [c for c in produtos.values() if c["em_promocao"]]
    assert len(promo) == 2
    for card in promo:
        assert card["preco_original"] > card["preco"]
        assert card["nome"]  # nome utilizável no dashboard


def test_atributos_do_titulo_sao_raros(produtos):
    """AGENTS.md §4.2: torra/processo só existem quando o título informa."""
    assert sum(1 for c in produtos.values() if c["torra"]) == 4
    assert sum(1 for c in produtos.values() if c["processo"]) == 1


def test_regressao_contra_snapshot(produtos, esperado):
    """Compara o parsing atual com o snapshot das mesmas páginas (alarme de layout)."""
    assert set(produtos) == set(esperado)
    for url, referencia in esperado.items():
        atual = produtos[url]
        assert atual["nome"] == referencia["nome"]
        assert atual["peso_g"] == referencia["peso_g"]
        assert atual["preco"] == referencia["preco"]
        assert atual["preco_original"] == referencia["preco_original"]
        assert atual["em_promocao"] == referencia["em_promocao"]


def test_url_canonica_remove_rastreio():
    assert ex.url_canonica("/cafe-x-123/p?utm_source=zap&skuId=7") == (
        "https://www.santaluzia.com.br/cafe-x-123/p?skuId=7"
    )


def _produtos_offline() -> list[dict]:
    todos: list[dict] = []
    for pagina in PAGINAS:
        todos.extend(ex.extrair_cards(ler_fixture(pagina)))
    return todos


# --------------------------------------------------------------------------
# Teste de rede (opt-in): valida contra a listagem ao vivo
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.getenv("CAFE_LIVE") != "1", reason="defina CAFE_LIVE=1 para rodar contra o site")
def test_listagem_ao_vivo():
    from scrapers import fetch

    estado = fetch.EstadoRede()
    with fetch.criar_client() as client:
        fetch.carregar_robots(client, estado)
        cards = ex.extrair_cards(fetch.baixar(client, config.LISTAGEM_URL, estado))
    assert len(cards) >= config.GUARDA_MIN_PRODUTOS
    assert all(c["preco"] for c in cards)
