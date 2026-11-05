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
    col_status, col_botao = st.columns([5, 1])
    with col_botao:
        if st.button("↻ Atualizar", help="Relê o banco agora (descarta o cache de 1 h)",
                     width="stretch"):
            st.cache_data.clear()
            st.rerun()
    with col_status:
        if coletas.empty:
            st.info("Nenhuma coleta registrada ainda. A coleta roda no GitHub Actions, 1x/dia às 20h (horário de Brasília).")
            return
        ultima = resumo["ultima_coleta"]
        quando = ultima.strftime("%d/%m/%Y %H:%M") if ultima is not None else "—"
        if resumo["ultimo_status"] == "ok":
            st.caption(f"Última coleta: {quando} (horário de Brasília) — status **ok**")
        else:
            # a mensagem vem da tabela `coletas` e explica a falha (timeout, 429, guarda...)
            detalhe = coletas.iloc[0].get("mensagem")
            extra = f" {detalhe}" if isinstance(detalhe, str) and detalhe.strip() else ""
            st.error(f"Última coleta: {quando} — status **{resumo['ultimo_status']}**.{extra} "
                     f"Veja o histórico do workflow no GitHub Actions.")


def _faixa_slider(rotulo: str, minimo: float, maximo: float, *, ajuda: str | None = None,
                  formato=dados.formatar_brl) -> tuple[float, float] | None:
    """Slider de faixa, tolerante a um único valor possível.

    `st.slider` com `min_value == max_value` levanta exceção no Streamlit 1.65 e
    derruba a página inteira (as abas somem). Com um só valor, mostramos o valor
    fixo e devolvemos `None` — filtro desligado, que é o comportamento correto:
    não há faixa a escolher.
    """
    if minimo == maximo:
        st.sidebar.caption(f"{rotulo}: {formato(minimo)}")
        return None
    return st.sidebar.slider(rotulo, float(minimo), float(maximo),
                             (float(minimo), float(maximo)), step=1.0, help=ajuda)


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
        faixa = _faixa_slider("Faixa de preço (R$)", minimo, maximo)
        # produtos sem peso têm R$/kg desconhecido: sem este dropna o slider recebe NaN
        com_kg = ativos["preco_por_kg"].dropna()
        if not com_kg.empty:
            kg_min, kg_max = float(com_kg.min()), float(com_kg.max())
            faixa_kg = _faixa_slider(
                "Faixa de R$/kg", kg_min, kg_max,
                ajuda="Produtos sem peso cadastrado continuam na lista (R$/kg não calculável).",
            )

    so_promo = st.sidebar.checkbox("Somente promoções")

    filtrado = dados.filtrar(
        ativos, busca=busca, marcas=marca, pesos=peso,
        faixa_preco=faixa, faixa_kg=faixa_kg, so_promo=so_promo,
    )

    # denominador = só ativos: `produtos` inclui os que já saíram da listagem
    st.sidebar.caption(f"{len(filtrado)} de {len(ativos)} produtos")
    return filtrado


VERDE_QUEDA = "#1a7f37"
VERMELHO_ALTA = "#c62828"


def _pintar_variacao(texto: str) -> str:
    """Verde para queda de preço, vermelho para alta (perspectiva de quem compra)."""
    if not isinstance(texto, str):
        return ""
    if texto.startswith("▼"):
        return f"color: {VERDE_QUEDA}; font-weight: 600"
    if texto.startswith("▲"):
        return f"color: {VERMELHO_ALTA}; font-weight: 600"
    return ""


def tabela(produtos: pd.DataFrame) -> None:
    colunas = {
        "nome": st.column_config.TextColumn("Produto", width="large"),
        "marca": st.column_config.TextColumn("Marca"),
        "peso_g": st.column_config.NumberColumn("Peso", format="%d g"),
        "preco": st.column_config.NumberColumn("Preço", format="R$ %.2f"),
        "preco_anterior": st.column_config.NumberColumn("Preço anterior", format="R$ %.2f"),
        "variacao_txt": st.column_config.TextColumn(
            "Variação", help="Contra a coleta anterior registrada (pode ser de mais de um dia atrás)"),
        "pct_sobre_piso": st.column_config.NumberColumn(
            "Acima do piso", format="%+.2f%%",
            help="Quanto o preço atual está acima do menor preço já registrado deste produto "
                 "(o piso inclui preços promocionais)"),
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

    # Styler (e não só column_config): a cor é o único canal que o column_config
    # do 1.65 não oferece, e a tabela tem 50+ linhas.
    exibicao = base_ord[list(colunas)].style.map(_pintar_variacao, subset=["variacao_txt"])

    st.dataframe(
        exibicao,
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
    opcoes = dados.opcoes_produto(ordenados)
    escolhido = st.selectbox("Produto", [rotulo for _, rotulo in opcoes], index=padrao)

    # seleciona pelo `produto_id`, não pelo nome: há homônimos na listagem
    produto_id = {rotulo: pid for pid, rotulo in opcoes}[escolhido]
    linha = ordenados[ordenados["produto_id"] == produto_id].iloc[0]
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


def _metric_extremo(coluna, rotulo: str, extremo: tuple[str, float] | None, *, sinal: int,
                    vazio: str, sem_variacao: str = "Precisa de pelo menos duas coletas") -> None:
    """Métrica de extremo (maior queda / maior alta).

    Antes, "sem queda" era exibido como "0,00%", que parece uma medição real. Se
    não há valor no sinal pedido, mostramos "—" e explicamos no tooltip.
    """
    if extremo is None:
        coluna.metric(rotulo, "—", help=sem_variacao)
        return
    nome, variacao = extremo
    if (sinal < 0 and variacao < 0) or (sinal > 0 and variacao > 0):
        coluna.metric(rotulo, dados.formatar_pct(variacao), help=nome)
    else:
        coluna.metric(rotulo, "—", help=vazio)


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

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Produtos ativos", resumo["ativos"])
    c2.metric("Em promoção", resumo["promocoes"])
    c3.metric("R$/kg mediano", dados.formatar_brl(resumo["preco_por_kg_mediano"]),
              help="Mediana do preço por quilo; a média seria distorcida pelos nanolotes")
    _metric_extremo(c4, "Maior queda", resumo["maior_queda"], sinal=-1,
                    vazio="Nenhum preço caiu entre as duas últimas coletas")
    _metric_extremo(c5, "Maior alta", resumo["maior_alta"], sinal=+1,
                    vazio="Nenhum preço subiu entre as duas últimas coletas")
    c6.metric("Fora da listagem", resumo["inativos"], help="Produtos que já não aparecem na categoria")

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
