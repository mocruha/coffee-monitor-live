"""Camada de dados do dashboard.

Regra: aqui é **somente leitura**. O painel nunca escreve no banco — quem
escreve é o scraper (`python -m scrapers.santa_luzia`). Como o Neon não oferece
um modo `ro` por conexão simples, a garantia é de política: nenhuma função
deste módulo emite comandos de escrita (há um teste que varre este arquivo por
isso: `tests/test_dashboard_dados.py::test_dashboard_nao_escreve`).

A connection string vem de `st.secrets["DATABASE_URL"]` (Streamlit Community
Cloud) com fallback para a variável de ambiente (execução local).

A variação é calculada com o `LAG` dentro de uma CTE sobre o histórico completo e
só depois filtrada para o snapshot mais recente. Se o filtro viesse antes do
`LAG`, cada produto apareceria uma única vez e a coluna de variação voltaria
`NULL` inteira — sem erro nenhum (ver AGENTS.md §6).
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import Engine, text

from scrapers import base, config

FUSO = ZoneInfo(config.TZ_EXIBICAO)

CONSULTA_ATUAL = """
WITH historico AS (
    SELECT produto_id,
           data_coleta,
           preco,
           preco_original,
           em_promocao,
           ROW_NUMBER() OVER (PARTITION BY produto_id ORDER BY data_coleta DESC) AS posicao,
           LAG(preco) OVER (PARTITION BY produto_id ORDER BY data_coleta) AS preco_anterior,
           LAG(data_coleta) OVER (PARTITION BY produto_id ORDER BY data_coleta) AS data_anterior
    FROM precos
)
SELECT p.id            AS produto_id,
       p.nome          AS nome,
       p.marca         AS marca,
       p.peso_g        AS peso_g,
       p.ativo         AS ativo,
       p.ultima_visao  AS ultima_visao,
       h.preco            AS preco,
       h.preco_original   AS preco_original,
       h.em_promocao      AS em_promocao,
       h.data_coleta      AS data_coleta,
       h.preco_anterior   AS preco_anterior,
       h.data_anterior    AS data_anterior
FROM historico h
JOIN produtos p ON p.id = h.produto_id
WHERE h.posicao = 1
"""

CONSULTA_HISTORICO = """
SELECT pc.data_coleta, pc.preco, pc.preco_original, pc.em_promocao
FROM precos pc
WHERE pc.produto_id = :produto_id
ORDER BY pc.data_coleta
"""

CONSULTA_COLETAS = """
SELECT id, iniciada_em, finalizada_em, paginas, requisicoes,
       itens_vistos, itens_gravados, status, mensagem
FROM coletas
ORDER BY id DESC
LIMIT :limite
"""


def resolver_url() -> str:
    """DATABASE_URL via `st.secrets` (Streamlit) ou variável de ambiente."""
    try:
        import streamlit as st

        if "DATABASE_URL" in st.secrets:
            return base.normalizar_url(st.secrets["DATABASE_URL"])
    except Exception:  # fora do runtime do Streamlit / secrets ausentes
        pass
    if not os.environ.get("DATABASE_URL"):
        raise base.ConfiguracaoAusente(
            "DATABASE_URL não definida. No Streamlit Cloud, configure em Secrets "
            "(veja .streamlit/secrets.toml.example); localmente, use .env."
        )
    return base.database_url()


def criar_engine(url: str | None = None) -> Engine:
    """Engine de leitura (mesma fonte única de conexão do scraper)."""
    return base.criar_engine(url or resolver_url())


def _localizar(serie: pd.Series) -> pd.Series:
    """UTC (como está no banco) -> horário de Brasília."""
    return pd.to_datetime(serie, utc=True).dt.tz_convert(FUSO)


def formatar_brl(valor: float | None) -> str:
    """1.234.56 -> 'R$ 1.234,56'."""
    if valor is None or pd.isna(valor):
        return "—"
    return "R$ " + f"{valor:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def formatar_brl_md(valor: float | None) -> str:
    """Igual a `formatar_brl`, mas com o `$` escapado para uso em `st.markdown`.

    Sem o escape o Streamlit trata "R$ 112,00" como fórmula LaTeX: o cifrão
    desaparece e o negrito em volta quebra.
    """
    return formatar_brl(valor).replace("$", "\\$")


def formatar_pct(valor: float | None) -> str:
    """Variação percentual com vírgula decimal e sinal: -20.0 -> '-20,00%'."""
    if valor is None or pd.isna(valor):
        return "—"
    return f"{valor:+.2f}".replace(".", ",") + "%"


def formatar_peso(peso_g: float | None) -> str:
    """250 -> '250g', 1000 -> '1kg', 1500 -> '1,5kg'; None/NaN -> texto explicativo.

    Sem isso, `f"{float('nan')}g"` imprime literalmente "nang" na tela.
    """
    if peso_g is None or pd.isna(peso_g):
        return "peso não informado"
    valor = float(peso_g)
    if valor < 1000:
        return f"{valor:g}g"
    return f"{valor / 1000:g}".replace(".", ",") + "kg"


def _na_faixa(serie: pd.Series, faixa: tuple[float, float] | None) -> pd.Series:
    """Máscara de faixa que **não descarta** valor desconhecido (`NaN`).

    `Series.between` devolve `False` para `NaN`, então um produto sem peso
    (R$/kg desconhecido) desaparecia de todas as abas mesmo com o slider na
    faixa completa. Desconhecido não é "fora da faixa".
    """
    if faixa is None:
        return pd.Series(True, index=serie.index)
    return serie.isna() | serie.between(*faixa)


def opcoes_produto(produtos: pd.DataFrame) -> list[tuple[int, str]]:
    """(produto_id, rótulo) para o selectbox do gráfico — nunca repete rótulo.

    `nome` não é único no schema (a chave é a `url`); na listagem real há dois
    pares de homônimos. Sem desambiguar, o selectbox mostra a mesma etiqueta
    duas vezes e o histórico do segundo produto fica inalcançável.
    """
    contagem = produtos["nome"].value_counts()
    rotulos = [
        f"{linha['nome']} · {formatar_peso(linha['peso_g'])}"
        if contagem[linha["nome"]] > 1 else linha["nome"]
        for _, linha in produtos.iterrows()
    ]
    repetidos = {r for r in rotulos if rotulos.count(r) > 1}  # mesmo nome e mesmo peso
    ids = [int(i) for i in produtos["produto_id"]]
    return [
        (pid, f"{rotulo} · #{pid}" if rotulo in repetidos else rotulo)
        for pid, rotulo in zip(ids, rotulos)
    ]


def filtrar(
    produtos: pd.DataFrame,
    *,
    busca: str = "",
    marcas: list[str] | None = None,
    pesos: list[int] | None = None,
    faixa_preco: tuple[float, float] | None = None,
    faixa_kg: tuple[float, float] | None = None,
    so_promo: bool = False,
) -> pd.DataFrame:
    """Aplica os filtros da sidebar. Vive aqui (e não no `app.py`) para ser testável."""
    filtrado = produtos
    if busca:
        # regex=False: a busca é texto literal; sem isso, "(" ou "+" derrubam a página
        filtrado = filtrado[filtrado["nome"].str.contains(busca, case=False, na=False, regex=False)]
    if marcas:
        filtrado = filtrado[filtrado["marca"].isin(marcas)]
    if pesos:
        filtrado = filtrado[filtrado["peso_g"].isin(pesos)]
    filtrado = filtrado[_na_faixa(filtrado["preco"], faixa_preco)]
    filtrado = filtrado[_na_faixa(filtrado["preco_por_kg"], faixa_kg)]
    if so_promo:
        filtrado = filtrado[filtrado["em_promocao"]]
    return filtrado


def carregar_produtos(engine: Engine | None = None) -> pd.DataFrame:
    """Um registro por produto com o preço atual e a variação contra a coleta anterior."""
    engine = engine or criar_engine()
    with engine.connect() as con:
        df = pd.read_sql_query(text(CONSULTA_ATUAL), con)

    if df.empty:
        return df

    df["preco_por_kg"] = (df["preco"] / df["peso_g"] * 1000).round(2)
    tem_anterior = df["preco_anterior"].notna() & (df["preco_anterior"] > 0)
    df["variacao_pct"] = pd.NA
    df.loc[tem_anterior, "variacao_pct"] = (
        100 * (df.loc[tem_anterior, "preco"] - df.loc[tem_anterior, "preco_anterior"])
        / df.loc[tem_anterior, "preco_anterior"]
    ).round(2)
    df["variacao_abs"] = (df["preco"] - df["preco_anterior"]).round(2)
    df["em_promocao"] = df["em_promocao"].astype(bool)
    df["ativo"] = df["ativo"].astype(bool)
    df["atualizado_em"] = _localizar(df["data_coleta"])
    df["visto_em"] = _localizar(df["ultima_visao"])
    return df


def carregar_historico(produto_id: int, engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or criar_engine()
    with engine.connect() as con:
        df = pd.read_sql_query(text(CONSULTA_HISTORICO), con, params={"produto_id": produto_id})
    if df.empty:
        return df
    df["data_local"] = _localizar(df["data_coleta"])
    df["em_promocao"] = df["em_promocao"].astype(bool)
    return df


def ultima_por_dia(historico: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por dia: a última coleta de cada dia (por `data_local`).

    Substitui o antigo filtro "hora == 20": esconde as coletas das 08h do
    histórico antigo e continua correto quando uma coleta atrasa (ex.: 21h) ou
    quando uma run manual de recuperação roda fora do horário.
    """
    if historico.empty:
        return historico
    ordenado = historico.sort_values("data_local")
    return ordenado.groupby(ordenado["data_local"].dt.date).tail(1)


def agregar_periodo(historico: pd.DataFrame, granularidade: str) -> pd.DataFrame:
    """Média de `preco`/`preco_kg` por dia, semana ou mês.

    Na granularidade "Dia" o período vira `date` (sem hora), para o eixo não
    exibir hora/segundo. O fuso já vem em Brasília; retirá-lo antes de virar
    período evita o aviso de "drop timezone" do pandas.
    """
    regra = {"Dia": "D", "Semana": "W", "Mês": "M"}[granularidade]
    com_periodo = historico.assign(
        periodo=historico["data_local"].dt.tz_localize(None).dt.to_period(regra).dt.start_time
    )
    agregado = com_periodo.groupby("periodo")[["preco", "preco_kg"]].mean().reset_index()
    if granularidade == "Dia":
        agregado["periodo"] = agregado["periodo"].dt.date
    return agregado


def carregar_coletas(limite: int = 20, engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or criar_engine()
    with engine.connect() as con:
        df = pd.read_sql_query(text(CONSULTA_COLETAS), con, params={"limite": limite})
    if df.empty:
        return df
    df["iniciada_local"] = _localizar(df["iniciada_em"])
    df["finalizada_local"] = _localizar(df["finalizada_em"])
    return df


def resumo(df: pd.DataFrame, coletas: pd.DataFrame) -> dict:
    """Números do topo do painel."""
    ativos = df[df["ativo"]] if not df.empty else df
    com_preco = ativos if ativos.empty else ativos[ativos["preco"].notna()]
    quedas = com_preco[com_preco["variacao_pct"].notna()] if not com_preco.empty else com_preco
    maior_queda = quedas.sort_values("variacao_pct").head(1) if not quedas.empty else quedas
    return {
        "produtos": len(df),
        "ativos": len(ativos),
        "inativos": int((~df["ativo"]).sum()) if not df.empty else 0,
        "promocoes": int(com_preco["em_promocao"].sum()) if not com_preco.empty else 0,
        # mediana, e não média: o R$/kg dos nanolotes distorce a média para cima
        "preco_por_kg_mediano": com_preco["preco_por_kg"].median() if not com_preco.empty else None,
        "ultima_coleta": coletas["iniciada_local"].iloc[0] if not coletas.empty else None,
        "ultimo_status": coletas["status"].iloc[0] if not coletas.empty else None,
        "maior_queda": (
            (maior_queda["nome"].iloc[0], maior_queda["variacao_pct"].iloc[0])
            if not maior_queda.empty
            else None
        ),
        "sem_variacao": int(com_preco["variacao_pct"].isna().sum()) if not com_preco.empty else 0,
    }
