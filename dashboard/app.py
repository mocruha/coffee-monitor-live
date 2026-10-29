"""Dashboard dos cafés em grãos do Casa Santa Luzia.

Rode com:
    streamlit run dashboard/app.py

O painel lê o Neon (PostgreSQL) em modo somente leitura — quem escreve é o
scraper (`python -m scrapers.santa_luzia`, disparado pelo GitHub Actions).
Filtros disponíveis: os que têm dado de verdade (peso, preço, R$/kg, promoção,
marca) mais busca por nome. A tabela mostra apenas os produtos que estão na
listagem do site; os que saíram continuam no banco, com histórico preservado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import dados  # noqa: E402
from scrapers import base  # noqa: E402

st.set_page_config(page_title="Cafés em grãos — Casa Santa Luzia", page_icon="☕", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False)
def carregar() -> tuple[pd.DataFrame, pd.DataFrame]:
    return dados.carregar_produtos(), dados.carregar_coletas()


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_historico(produto_id: int) -> pd.DataFrame:
    """Histórico de um produto, com cache — evita bater no Neon a cada clique."""
    return dados.carregar_historico(produto_id)


def barra_de_status(coletas: pd.DataFrame, resumo: dict) -> None:
    if coletas.empty:
        st.info("Nenhuma coleta registrada ainda. A coleta roda no GitHub Actions, 1x/dia às 20h (horário de Brasília).")
        return
    ultima = resumo["ultima_coleta"]
    quando = ultima.strftime("%d/%m/%Y %H:%M") if ultima is not None else "—"
    if resumo["ultimo_status"] == "ok":
        st.caption(f"Última coleta: {quando} (horário de Brasília) — status **ok**")
    else:
        st.error(f"Última coleta: {quando} — status **{resumo['ultimo_status']}**. "
                 f"Veja o histórico do workflow no GitHub Actions.")


def sidebar(produtos: pd.DataFrame) -> pd.DataFrame:
    ativos = produtos[produtos["ativo"]]
    st.sidebar.header("Filtros")

    busca = st.sidebar.text_input("Buscar por nome", placeholder="ex.: orfeu, zaro, nanolote")
    marcas = sorted(m for m in ativos["marca"].dropna().unique())
    marca = st.sidebar.multiselect("Marca", marcas)
    por_rotulo = {dados.formatar_peso(p): p for p in ativos["peso_g"].dropna().unique()}
    peso = [por_rotulo[r] for r in
            st.sidebar.multiselect("Peso", sorted(por_rotulo), help="Pesos existentes na categoria")]

    faixa = faixa_kg = None
    if not ativos.empty:
        minimo, maximo = float(ativos["preco"].min()), float(ativos["preco"].max())
        faixa = st.sidebar.slider("Faixa de preço (R$)", minimo, maximo, (minimo, maximo), step=1.0)
        # produtos sem peso têm R$/kg desconhecido: sem este dropna o slider recebe NaN
        com_kg = ativos["preco_por_kg"].dropna()
        if not com_kg.empty:
            kg_min, kg_max = float(com_kg.min()), float(com_kg.max())
            faixa_kg = st.sidebar.slider("Faixa de R$/kg", kg_min, kg_max, (kg_min, kg_max), step=1.0,
                                         help="Produtos sem peso cadastrado continuam na lista "
                                              "(R$/kg não calculável).")

    so_promo = st.sidebar.checkbox("Somente promoções")

    filtrado = dados.filtrar(
        ativos, busca=busca, marcas=marca, pesos=peso,
        faixa_preco=faixa, faixa_kg=faixa_kg, so_promo=so_promo,
    )

    st.sidebar.caption(f"{len(filtrado)} de {len(produtos)} produtos")
    return filtrado


def tabela(produtos: pd.DataFrame) -> None:
    colunas = {
        "nome": st.column_config.TextColumn("Produto", width="large"),
        "marca": st.column_config.TextColumn("Marca"),
        "peso_g": st.column_config.NumberColumn("Peso", format="%d g"),
        "preco": st.column_config.NumberColumn("Preço", format="R$ %.2f"),
        "preco_anterior": st.column_config.NumberColumn("Preço anterior", format="R$ %.2f"),
        "variacao_txt": st.column_config.TextColumn(
            "Variação", help="Contra a coleta anterior registrada (pode ser de mais de um dia atrás)"),
        "preco_por_kg": st.column_config.NumberColumn("R$/kg", format="R$ %.2f"),
        "em_promocao": st.column_config.CheckboxColumn("Promoção"),
        "atualizado_em": st.column_config.DatetimeColumn("Coleta", format="DD/MM/YYYY HH:mm"),
    }
    col_ord, col_dir = st.columns([3, 1])
    with col_ord:
        ordenacao = st.selectbox(
            "Ordenar por",
            ["Variação", "Preço", "R$/kg", "Peso", "Nome"],
            label_visibility="collapsed",
        )
    with col_dir:
        ascendente = st.radio(
            "Direção",
            ["Asc", "Desc"],
            horizontal=True,
            label_visibility="collapsed",
        ) == "Asc"
    chave = {"Variação": "variacao_pct", "Preço": "preco", "R$/kg": "preco_por_kg",
             "Peso": "peso_g", "Nome": "nome"}[ordenacao]

    visao = produtos.assign(variacao_txt=produtos["variacao_pct"].map(dados.formatar_pct))
    base_ord = visao.sort_values(chave, ascending=ascendente, na_position="last")

    st.dataframe(
        base_ord[list(colunas)],
        column_config=colunas,
        hide_index=True,
        width="stretch",
        height=620,
    )

    # Avisa quando a variação compara dias não consecutivos (houve dia sem coleta).
    lacuna_h = (
        pd.to_datetime(produtos["data_coleta"], utc=True)
        - pd.to_datetime(produtos["data_anterior"], utc=True)
    ).dt.total_seconds() / 3600
    longe = produtos["variacao_pct"].notna() & lacuna_h.gt(36)
    if longe.any():
        st.caption(f"{int(longe.sum())} produto(s) comparam com uma coleta de mais de um dia atrás "
                   f"(houve dia sem coleta).")

    promocoes = produtos[produtos["em_promocao"]]
    if not promocoes.empty:
        st.subheader("Promoções ativas")
        for _, linha in promocoes.iterrows():
            desconto = 100 * (1 - linha["preco"] / linha["preco_original"])
            st.markdown(
                f"**{linha['nome']}** — de {dados.formatar_brl_md(linha['preco_original'])} "
                f"por {dados.formatar_brl_md(linha['preco'])} ({desconto:.0f}% off) · "
                f"{dados.formatar_peso(linha['peso_g'])} · {dados.formatar_brl_md(linha['preco_por_kg'])}/kg"
            )


def grafico(produtos: pd.DataFrame) -> None:
    st.caption("Usa a **última coleta de cada dia** (no histórico antigo, a das 20h); "
               "o ponto é o preço médio do período escolhido.")
    if produtos.empty:
        st.info("Nenhum produto no filtro atual.")
        return

    # Um produto por vez; o de melhor R$/kg já vem selecionado.
    ordenados = produtos.sort_values("nome").reset_index(drop=True)
    padrao = int(ordenados["preco_por_kg"].idxmin()) if ordenados["preco_por_kg"].notna().any() else 0
    escolhido = st.selectbox("Produto", ordenados["nome"].tolist(), index=padrao)

    linha = ordenados[ordenados["nome"] == escolhido].iloc[0]
    sem_peso = pd.isna(linha["peso_g"])

    # Uma linha por dia (a última coleta do dia), com cache. Assim as coletas das
    # 08h do histórico antigo ficam de fora e uma coleta atrasada não some.
    historico = dados.ultima_por_dia(carregar_historico(int(linha["produto_id"])))
    if len(historico) < 2:
        st.info("Menos de duas coletas diárias para este produto — a evolução aparece a partir da segunda.")
        return
    if sem_peso:
        historico = historico.assign(preco_kg=float("nan"))
    else:
        historico = historico.assign(preco_kg=historico["preco"] / linha["peso_g"] * 1000)

    col_gran, col_met = st.columns(2)
    with col_gran:
        granularidade = st.segmented_control(
            "Agrupamento", ["Dia", "Semana", "Mês"], default="Dia",
            label_visibility="collapsed",
        ) or "Dia"
    with col_met:
        opcoes = ["Preço do pacote"] if sem_peso else ["Preço do pacote", "Preço por quilo"]
        metrica = st.segmented_control(
            "Métrica", opcoes, default=opcoes[0], label_visibility="collapsed",
        ) or opcoes[0]
    if sem_peso:
        st.caption("Produto sem peso cadastrado — só o preço do pacote pode ser mostrado.")

    agregado = dados.agregar_periodo(historico, granularidade)

    coluna = "preco" if metrica == "Preço do pacote" else "preco_kg"
    figura = px.line(agregado, x="periodo", y=coluna, markers=True,
                     labels={"periodo": "Data (horário de Brasília)", coluna: metrica})
    figura.update_xaxes(tickformat="%d/%m/%Y", hoverformat="%d/%m/%Y")
    figura.update_layout(margin=dict(t=10))
    st.plotly_chart(figura, width="stretch")


def destaques(produtos: pd.DataFrame) -> None:
    com_variacao = produtos[produtos["variacao_pct"].notna()]
    esquerda, direita = st.columns(2)

    with esquerda:
        st.subheader("Maiores quedas desde a coleta anterior")
        quedas = com_variacao[com_variacao["variacao_pct"] < 0].sort_values("variacao_pct").head(10)
        if com_variacao.empty:
            st.info("Nenhuma variação registrada ainda — é preciso pelo menos duas coletas por produto.")
        elif quedas.empty:
            st.info("Nenhum produto caiu de preço entre as duas últimas coletas.")
        else:
            for _, linha in quedas.iterrows():
                st.markdown(
                    f"**{dados.formatar_pct(linha['variacao_pct'])}** · {linha['nome']} "
                    f"({dados.formatar_peso(linha['peso_g'])}) — {dados.formatar_brl_md(linha['preco_anterior'])} → "
                    f"{dados.formatar_brl_md(linha['preco'])}"
                )

    with direita:
        st.subheader("Menor preço por quilo")
        for _, linha in produtos.dropna(subset=["preco_por_kg"]).sort_values("preco_por_kg").head(10).iterrows():
            st.markdown(
                f"**{dados.formatar_brl_md(linha['preco_por_kg'])}/kg** · {linha['nome']} "
                f"({dados.formatar_peso(linha['peso_g'])}) — {dados.formatar_brl_md(linha['preco'])}"
            )

    parados = produtos[produtos["variacao_pct"].isna()]
    if not parados.empty:
        st.caption(f"{len(parados)} produtos ainda sem variação calculável (só uma coleta registrada).")


def main() -> None:
    st.title("☕ Cafés em grãos — Casa Santa Luzia")

    try:
        produtos, coletas = carregar()
    except base.ConfiguracaoAusente as erro:
        st.error(f"Configuração ausente: {erro}")
        st.caption("No Streamlit Cloud, defina `DATABASE_URL` em **Secrets** "
                   "(veja `.streamlit/secrets.toml.example`).")
        return
    except Exception as erro:  # noqa: BLE001 - falha de conexão não deve derrubar a página
        st.error(f"Não consegui ler o banco: {erro}")
        return

    if produtos.empty:
        st.warning("Banco ainda sem produtos. A primeira coleta do GitHub Actions preenche.")
        return

    resumo = dados.resumo(produtos, coletas)
    barra_de_status(coletas, resumo)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Produtos ativos", resumo["ativos"])
    c2.metric("Em promoção", resumo["promocoes"])
    c3.metric("R$/kg mediano", dados.formatar_brl(resumo["preco_por_kg_mediano"]),
              help="Mediana do preço por quilo; a média seria distorcida pelos nanolotes")
    if resumo["maior_queda"]:
        nome, variacao = resumo["maior_queda"]
        if variacao < 0:
            c4.metric("Maior queda", dados.formatar_pct(variacao), help=nome)
        else:
            c4.metric("Maior queda", "0,00%",
                      help="Nenhum preço caiu entre as duas últimas coletas")
    else:
        c4.metric("Maior queda", "—", help="Precisa de pelo menos duas coletas")
    c5.metric("Fora da listagem", resumo["inativos"], help="Produtos que já não aparecem na categoria")

    filtrado = sidebar(produtos)
    tabela_aba, evolucao_aba, destaques_aba = st.tabs(
        ["Produtos", "Evolução de preço", "Destaques"]
    )
    with tabela_aba:
        tabela(filtrado)
    with evolucao_aba:
        grafico(filtrado)
    with destaques_aba:
        destaques(filtrado)


main()
