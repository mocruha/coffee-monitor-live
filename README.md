# Monitor de Cafés em Grãos — Casa Santa Luzia

**[Dashboard ao vivo → coffee-monitor-live.streamlit.app](https://coffee-monitor-live.streamlit.app/)**

Monitoramento de preços dos **cafés em grãos** vendidos no site do Casa Santa
Luzia, com histórico no **Neon (PostgreSQL)** e dashboard **Streamlit**.

A especificação do projeto é o `AGENTS.md`. A seção **§11 — Fatos verificados na
fonte** registra o que a recon de 13/10/2026 encontrou e o que mudou em relação
ao plano original.

## Arquitetura

Três serviços desacoplados que se comunicam **apenas pelo banco**:

| Serviço | Onde roda | Responsabilidade |
|---|---|---|
| Scraper | **GitHub Actions** (cron 2x/dia) | Coletar a listagem e fazer INSERT/UPDATE no Neon. Sem estado local. |
| Banco | **Neon (PostgreSQL)** | Única persistência: `produtos`, `precos`, `coletas`. |
| Dashboard | **Streamlit Community Cloud** | Ler o Neon e exibir. Somente leitura. |

## Como rodar localmente

```bash
# dependências
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# configure o banco e o contato
cp .env.example .env            # edite DATABASE_URL e CAFE_CONTATO

# testes (offline, SQLite, usam o HTML real salvo em tests/fixtures/)
.venv/bin/pytest

# testes contra um PostgreSQL real (opcional)
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/db .venv/bin/pytest

# teste contra o site ao vivo (respeita robots.txt e os limites do §5)
CAFE_LIVE=1 .venv/bin/pytest -k ao_vivo

# diagnóstico do parser — NÃO grava no banco
.venv/bin/python scripts/validar_parser.py

# coleta de verdade: baixa, valida e grava o snapshot no Neon
.venv/bin/python -m scrapers.santa_luzia

# ensaio sem gravar nada (útil antes de mexer em seletor)
.venv/bin/python -m scrapers.santa_luzia --dry-run

# painel (abre em http://localhost:8501)
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # edite DATABASE_URL
.venv/bin/streamlit run dashboard/app.py
```

Códigos de saída de `scrapers.santa_luzia`: `0` coleta ok, `1` erro (inclui
`DATABASE_URL` ausente), `2` abortada pela guarda de sanidade ou pela rede.

## Segredos

- **GitHub Actions:** `Settings > Secrets and variables > Actions` →
  `DATABASE_URL` e `CAFE_CONTATO`.
- **Streamlit Cloud:** painel do app → *Secrets* (mesmo conteúdo de
  `.streamlit/secrets.toml.example`).
- O código lê `os.environ["DATABASE_URL"]` (scraper) e
  `st.secrets["DATABASE_URL"]` (dashboard). **Nada** de credencial no repositório:
  `.env` e `.streamlit/secrets.toml` estão no `.gitignore`.

## Estrutura

```
scrapers/
  base.py           conexão com o Neon (única fonte de DATABASE_URL)
  config.py         URL da listagem, limites de requisição, marcas conhecidas
  models.py         SQLAlchemy: produtos, precos, coletas
  fetch.py          cliente HTTPX + robots.txt + delay + backoff 429/5xx + teto
  extractors.py     parsing dos cards (título, preço, promoção, torra, processo, marca)
  persistencia.py   guarda de sanidade + upsert + auditoria
  santa_luzia.py    entry point: coleta e grava
dashboard/
  app.py            painel Streamlit
  dados.py          consultas de leitura (variação via LAG em CTE)
scripts/
  validar_parser.py diagnóstico ao vivo, sem gravar
tests/
  conftest.py       engine por teste (SQLite local / Postgres no CI)
  fixtures/         8 páginas reais (7 com produtos + a vazia) + snapshot dos 51
.github/workflows/
  scraper.yml       coleta 2x/dia (11h e 23h UTC = 08h e 20h BRT)
  testes.yml        pytest com service container PostgreSQL
```

## Decisões de projeto tomadas com o usuário

| Tema | Decisão |
|---|---|
| Escopo | Somente café em grãos, pela categoria própria `matinais/cafe/cafe-em-graos` |
| Preço atual | O único preço exposto no card (`sellingPrice`); não existe preço de Pix na listagem |
| Preço original | O "De" riscado (`listPrice`), quando existe |
| Histórico | Uma linha por produto por coleta, sempre (2x/dia) |
| Variação | Comparada com a coleta imediatamente anterior |
| Produto que some | `ativo=FALSE` + `ultima_visao`, sai da tabela principal e mantém o histórico |
| Alertas | Nenhum nesta fase; só dashboard |
| Falha de layout | Aborta sem gravar se vier com menos de 5 produtos ou queda > 50% de itens |
| Marca | Não existe no card; derivada do título por lista editável, NULL quando não casar |
| Fuso | Banco em UTC; conversão para America/Sao_Paulo só na exibição |
| Agendamento | GitHub Actions 2x/dia (substituiu o cronjob do Hermes em 13/10/2026) |
| Banco | Neon (PostgreSQL), começando vazio (o histórico SQLite não foi migrado) |
| Exclusão mútua | `concurrency` do workflow (substitui o lock em arquivo) |
