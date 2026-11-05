# Monitor de Cafés em Grãos — Casa Santa Luzia

**[Dashboard ao vivo → coffee-monitor-live.streamlit.app](https://coffee-monitor-live.streamlit.app/)** — público, sem login.

Monitoramento dos **cafés em grãos** vendidos no site do Casa Santa Luzia:
coleta diária da listagem, histórico de preços no **Neon (PostgreSQL)** e
dashboard **Streamlit** somente leitura.

Especificação, modelo de dados e limites de requisição: [`AGENTS.md`](AGENTS.md) (§4–§7).

## Dashboard

![Aba de produtos: filtros, métricas, tabela com variação, piso e R$/kg](docs/img/dashboard-produtos.png)

![Aba de evolução: preço do pacote ao longo de 21 dias, com granularidade dia/semana/mês](docs/img/dashboard-evolucao.png)

- **Filtros** (sidebar): marca, peso, faixa de preço, faixa de R$/kg, "somente promoções" e busca por nome.
- **Tabela**: preço atual, preço anterior, variação percentual, **"acima do piso"** (quanto o preço está acima do menor preço já registrado do produto) e **R$/kg**, para comparar embalagens de 250 g com 1 kg.
- **Evolução e destaques**: série por produto em dia/semana/mês, maiores quedas e menor preço por quilo.
- **Somente leitura**: o painel nunca escreve no banco; o cache de consulta é de 1 h, e o botão **↻ Atualizar** relê na hora.

> Capturas geradas offline a partir das páginas reais salvas em `tests/fixtures/`; a série anterior ao
> último dia é sintética, só para ilustrar a variação. O app ao vivo usa as coletas reais do Neon.

## Arquitetura

Três serviços desacoplados que se comunicam **apenas pelo banco**:

| Serviço | Onde roda | Responsabilidade |
|---|---|---|
| Scraper | **GitHub Actions**, 1x/dia às 20h (`0 23 * * *` UTC) | Coletar a listagem de `matinais/cafe/cafe-em-graos` e fazer INSERT/UPDATE no Neon. Sem estado local. |
| Banco | **Neon (PostgreSQL)** | Única persistência: `produtos`, `precos`, `coletas`. |
| Dashboard | **Streamlit Community Cloud** | Ler o Neon e exibir. Somente leitura. |

## Rodando localmente

**Setup** — Python 3.12 e um `.venv`:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env      # preencha DATABASE_URL e CAFE_CONTATO
```

**Testes** — a suíte roda offline, em SQLite, contra o HTML real de `tests/fixtures/`:

```bash
.venv/bin/pytest                                                                    # offline (padrão)
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/db .venv/bin/pytest  # contra PostgreSQL
CAFE_LIVE=1 .venv/bin/pytest -k ao_vivo                                             # contra o site ao vivo
```

**Coleta:**

```bash
.venv/bin/python scripts/validar_parser.py             # diagnóstico do parser
.venv/bin/python -m scrapers.santa_luzia --dry-run     # baixa e valida
.venv/bin/python -m scrapers.santa_luzia               # coleta e grava no Neon
```

**Dashboard** (abre em <http://localhost:8501>):

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # preencha DATABASE_URL
.venv/bin/streamlit run dashboard/app.py
```

## Configuração

| Chave | Onde é lida | Onde fica |
|---|---|---|
| `DATABASE_URL` | `os.environ` (scraper) e `st.secrets` (dashboard) | Secret do repositório (Actions) e do app (Streamlit Cloud) |
| `CAFE_CONTATO` | `os.environ` | Secret do repositório; entra no `User-Agent` para identificação |

`.env` e `.streamlit/secrets.toml` estão no `.gitignore`; só o `.example` vai para o
Git. Nenhuma credencial é versionada.

## Estrutura

```
scrapers/
  base.py           conexão com o Neon (única fonte de DATABASE_URL)
  config.py         URL da listagem, limites de requisição, marcas conhecidas
  fetch.py          cliente HTTPX + robots.txt + delay + backoff 429/5xx + teto
  extractors.py     parsing dos cards (título, preço, promoção, torra, processo, marca)
  models.py         SQLAlchemy: produtos, precos, coletas
  persistencia.py   guarda de sanidade + upsert + auditoria
  santa_luzia.py    entry point: coleta e grava
dashboard/
  app.py            painel Streamlit
  dados.py          consultas de leitura (variação via LAG em CTE)
scripts/
  validar_parser.py diagnóstico ao vivo, sem gravar
  keepalive.py      visita o app no Streamlit Cloud (Playwright)
tests/              suíte offline; fixtures/ = 8 páginas reais (7 com produtos + a vazia)
docs/               revisão do dashboard, plano de melhorias e pendências
.github/workflows/
  scraper.yml       coleta 1x/dia às 20h (23h UTC)
  testes.yml        pytest com service container PostgreSQL
  keepalive.yml     visita o app 4x/dia, contra a hibernação de 12h
```

## Operação

- **Coleta diária** às 20h (23h UTC) via GitHub Actions; `concurrency` garante que uma
  execução nova espera a anterior terminar. Disparo manual: `gh workflow run scraper.yml`;
  histórico: `gh run list --workflow=scraper.yml`.
- **Guarda de sanidade**: antes de gravar, aborta sem snapshot se vierem menos de 5
  produtos, se houver queda acima de 50% em relação à coleta anterior ou se mais de 10%
  dos cards vierem sem preço — uma mudança de layout não envenena o histórico.
- **Hibernação**: o Streamlit Community Cloud dorme após 12h sem tráfego; o
  `keepalive.yml` visita o app 4x/dia para mantê-lo acordado.
- **Códigos de saída** de `scrapers.santa_luzia`: `0` coleta ok, `1` erro (inclui
  `DATABASE_URL` ausente), `2` abortada pela guarda de sanidade ou pela rede.
- **Cache do painel**: 1h por consulta, com botão **↻ Atualizar** na barra de status.
