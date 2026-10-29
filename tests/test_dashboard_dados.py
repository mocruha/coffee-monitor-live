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

import pandas as pd
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


# --- Gráfico de evolução: "última coleta do dia" e agregação (PRs #2/#4) ---

def _historico_brt() -> pd.DataFrame:
    """3 dias com coleta às 08h e 20h; o 2º dia tem também uma coleta atrasada às 21h.

    Horários em UTC: 08h BRT = 11h UTC, 20h BRT = 23h UTC, 21h BRT = 00h UTC do dia seguinte.
    """
    coleta = pd.to_datetime([
        "2026-10-13 11:00", "2026-10-13 23:00",
        "2026-10-14 11:00", "2026-10-14 23:00", "2026-10-15 00:00",
        "2026-10-15 11:00", "2026-10-15 23:00",
    ], utc=True)
    df = pd.DataFrame({"data_coleta": coleta, "preco": [1.0, 10.0, 2.0, 20.0, 21.0, 3.0, 30.0]})
    df["data_local"] = df["data_coleta"].dt.tz_convert("America/Sao_Paulo")
    df["preco_kg"] = df["preco"] * 4
    return df


def test_ultima_por_dia_esconde_as_08h():
    h = dados.ultima_por_dia(_historico_brt())
    assert len(h) == 3, "deve sobrar um ponto por dia"
    assert h["data_local"].dt.hour.tolist() == [20, 21, 20]


def test_ultima_por_dia_mantem_coleta_atrasada():
    """Regressão: o filtro antigo (hora == 20) descartava a coleta das 21h."""
    h = dados.ultima_por_dia(_historico_brt())
    dia_14 = h[h["data_local"].dt.date == pd.Timestamp("2026-10-14").date()]
    assert dia_14["preco"].iloc[0] == 21.0, "a última coleta do dia (21h) deve sobreviver"


def test_agregar_periodo_dia_usa_data_sem_hora():
    h = dados.ultima_por_dia(_historico_brt())
    agg = dados.agregar_periodo(h, "Dia")
    assert len(agg) == 3
    assert agg["preco"].tolist() == [10.0, 21.0, 30.0]
    # datetime.date não tem hora: é o que impede o eixo de mostrar hora/segundo
    assert all(not hasattr(p, "hour") for p in agg["periodo"])


def test_agregar_periodo_semana_e_mes():
    h = dados.ultima_por_dia(_historico_brt())
    semana = dados.agregar_periodo(h, "Semana")
    assert len(semana) == 1, "os 3 dias caem na mesma semana ISO"
    assert semana["preco"].iloc[0] == pytest.approx((10 + 21 + 30) / 3)
    mes = dados.agregar_periodo(h, "Mês")
    assert len(mes) == 1
    assert mes["preco"].iloc[0] == pytest.approx((10 + 21 + 30) / 3)


# --- Filtros do painel: produto sem peso não pode desaparecer (PR fix/A-1) ---

def _sem_peso(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Simula a loja removendo o peso do título: `peso_g` e `preco_por_kg` viram NaN.

    É o cenário do bug A-1: o produto continua ativo e coletado, mas sem R$/kg.
    """
    alterado = df.copy()
    alvo = alterado[alterado["ativo"]].index[0]
    alterado.loc[alvo, "peso_g"] = pd.NA
    alterado.loc[alvo, "preco_por_kg"] = pd.NA
    return alterado, int(alterado.loc[alvo, "produto_id"])


def test_filtro_nao_esconde_produto_sem_peso(banco):
    """Regressão A-1: `between` devolve False para NaN e sumia com o produto de todas as abas."""
    df = dados.carregar_produtos(banco)
    alterado, alvo = _sem_peso(df)
    ativos = alterado[alterado["ativo"]]

    filtrado = dados.filtrar(ativos, faixa_kg=(ativos["preco_por_kg"].min(), ativos["preco_por_kg"].max()))
    assert alvo in set(filtrado["produto_id"]), "produto sem peso sumiu do filtro de R$/kg"

    # estreitar a faixa de R$/kg também não pode esconder: desconhecido não é "fora da faixa"
    estreitado = dados.filtrar(ativos, faixa_kg=(500.0, 600.0))
    assert alvo in set(estreitado["produto_id"])


def test_formatar_peso_nulo_nao_vira_nang():
    """R-2: f'{float('nan')}g' renderiza 'nang' na UI."""
    assert dados.formatar_peso(None) == "peso não informado"
    assert dados.formatar_peso(float("nan")) == "peso não informado"
    assert dados.formatar_peso(250) == "250g"
    assert dados.formatar_peso(1000) == "1kg"
    # A-6: peso acima de 1 kg não pode virar "1kg"
    assert dados.formatar_peso(1500) == "1,5kg"


def test_filtro_de_peso_e_marca_continuam_funcionando(banco):
    df = dados.carregar_produtos(banco)
    ativos = df[df["ativo"]]
    duzentos_e_cinquenta = dados.filtrar(ativos, pesos=[250])
    assert set(duzentos_e_cinquenta["peso_g"]) == {250}

    marca = ativos["marca"].dropna().iloc[0]
    da_marca = dados.filtrar(ativos, marcas=[marca])
    assert set(da_marca["marca"]) == {marca}


# --- Gráfico: produtos homônimos precisam de rótulo único (A-2) ---

def test_opcoes_produto_nao_colidem_com_homonimos(banco):
    """`nome` não é único (a chave é a url): 2 pares reais na listagem."""
    df = dados.carregar_produtos(banco)
    opcoes = dados.opcoes_produto(df[df["ativo"]])

    assert len(opcoes) == 51
    rotulos = [rotulo for _, rotulo in opcoes]
    assert len(set(rotulos)) == len(rotulos), "rótulos repetidos: o selectbox esconde produtos"

    rituais = [(pid, r) for pid, r in opcoes if r.startswith("Café Rituais Grão 3 Corações")]
    assert len(rituais) == 2, "os dois homônimos devem aparecer"
    assert rituais[0][1] != rituais[1][1]


def test_opcoes_produto_mesmo_nome_e_peso_ainda_distingue():
    df = pd.DataFrame({
        "produto_id": [1, 2],
        "nome": ["Café X", "Café X"],
        "peso_g": [500.0, 500.0],
    })
    opcoes = dados.opcoes_produto(df)
    assert [pid for pid, _ in opcoes] == [1, 2]
    assert opcoes[0][1] != opcoes[1][1]


# --- C-4: preço atual vs piso histórico do próprio produto ---

def test_pct_sobre_piso_usa_o_historico_completo(banco):
    """`MIN() OVER` precisa rodar na CTE, antes do filtro `posicao = 1`.

    Se viesse depois, o piso seria o preço de hoje e todo mundo ficaria em 0%.
    """
    df = dados.carregar_produtos(banco)
    queda = df[df["variacao_pct"] < 0].iloc[0]
    assert queda["pct_sobre_piso"] == pytest.approx(0.0)

    alta = df[df["variacao_pct"] > 0].iloc[0]
    esperado = 100 * (alta["preco"] / alta["preco_anterior"] - 1)
    assert alta["pct_sobre_piso"] == pytest.approx(esperado, abs=0.01)


def test_piso_nao_sobe_quando_o_preco_sobe(engine, cards):
    """O piso é o menor preço já registrado; ficar acima dele é o indicador de "compro agora?"""
    alvo = list(cards)[2]
    preco_inicial = cards[alvo]["preco"]
    terceira = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)

    with Session(engine) as sessao:
        persistencia.persistir(sessao, cards, PRIMEIRA)
        sessao.commit()
        persistencia.persistir(sessao, {**cards, alvo: {**cards[alvo], "preco": preco_inicial + 10}}, SEGUNDA)
        sessao.commit()
        persistencia.persistir(sessao, {**cards, alvo: {**cards[alvo], "preco": preco_inicial + 20}}, terceira)
        sessao.commit()
        produto_id = sessao.query(models.Produto.id).filter_by(url=alvo).scalar()

    df = dados.carregar_produtos(engine)
    linha = df[df["produto_id"] == produto_id].iloc[0]
    assert linha["preco_piso"] == pytest.approx(preco_inicial, abs=0.01)
    assert linha["pct_sobre_piso"] == pytest.approx(100 * 20 / preco_inicial, abs=0.01)


def test_busca_e_texto_literal_nao_regex(banco):
    """N-1: sem `regex=False`, digitar '(' ou '+' no campo de busca derrubava a página."""
    df = dados.carregar_produtos(banco)
    assert dados.filtrar(df, busca="(").empty
    # '+' existe em "Coffee++": a busca é literal, não regex ('+' sozinho seria "uma ou mais")
    assert len(dados.filtrar(df, busca="+")) == 2
    assert len(dados.filtrar(df, busca="rituais")) == 4  # Expresso, Grão (2 homônimos) e Clássico
