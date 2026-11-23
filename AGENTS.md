# AGENTS.md — Monitor de Cafés em Grãos (Casa Santa Luzia)

> Contratos e decisões do projeto Santa Luzia. O que se deduz do repositório
> (estrutura, schema, comandos, stack) vive no código e no `README.md`; aqui
> fica apenas o que não se deduz. Seções citadas por comentários no código
> (§2, §4–§7, §11) mantêm a numeração.

## 1. Missão

Construir e manter um sistema de monitoramento de preços e modelos de **cafés em grãos** vendidos exclusivamente no site do **Casa Santa Luzia** (`santaluzia.com.br`). O sistema deve:

- Coletar dados **uma vez por dia** (20h, horário de Brasília).
- **Extrair tudo a partir da página de listagem** (cards de produto), sem acessar páginas de detalhes.
- Armazenar histórico de preços para calcular variações.
- Disponibilizar um **dashboard web** com filtros por peso, marca, preço, R$/kg e promoção.

---

## 2. Stack Tecnológica

Três serviços independentes acoplados **apenas pelo banco PostgreSQL (Neon)**.
Dependências e versões: `requirements.txt` / `requirements-dev.txt`.

- Scraper: **Python 3.12**, **HTTPX**, **BeautifulSoup4 + lxml**, agendado por **GitHub Actions** (cron), sem estado local.
- Persistência: **Neon (PostgreSQL)**, **psycopg 3** + **SQLAlchemy 2.x**. `DATABASE_URL` é segredo, nunca no código (§7.5).
- Dashboard: **Streamlit + Plotly** na Community Cloud, público, sem login, **somente leitura**, cache de 1 h.
- **Playwright não é dependência do runtime** — só existe no workflow `keepalive.yml`. Sem Airflow, sem APScheduler.
- Testes offline em SQLite; o CI roda a mesma suíte contra PostgreSQL.

---

## 3. Arquitetura do Sistema

Scraper (Actions) **escreve** no Neon; dashboard (Streamlit Cloud) **lê**. Nada mais persiste. Diagrama e responsabilidades: `README.md` §Arquitetura.

- Exclusão mútua entre execuções: `concurrency` do workflow (§7.3) — não há lock em arquivo.
- Idempotência: URL única do produto (§4.3).

---

## 4. Estratégia de Extração (somente listagem)

### 4.1 Fontes de dados disponíveis no card

**Fonte A — título:** padrão `Café <nome> - <peso>g`. Regra de parsing (implementada em `scrapers/extractors.py::parse_titulo`):

1. Normalizar travessões e hífens tipográficos (`–`, `—`, `−`) para `-`.
2. Tomar o **último** token `<número><g|kg>` do título como peso (unidade obrigatória; números no nome não confundem).
3. Nome = tudo antes desse token, sem separador nas pontas. `g` → valor direto; `kg` → × 1000.

O regex ancorado original falhava em 38/51 títulos reais; a regressão está em `tests/test_extractors.py::test_regex_original_do_agents_md_falhava`.

**Fonte B — seletores do card (contrato de extração):**

- Preço atual: span `[class*="sellingPrice"]` — **único** preço; não existe preço de Pix na listagem.
- Preço original: span `[class*="listPrice"]`, só em promoção. `em_promocao = listPrice > sellingPrice`.
- URL: `a.clearLink` — chave única, absoluta, sem parâmetros de rastreio.
- Nome: span `[class*="productName"]`.
- Card: `[class*="galleryItem"]`; **página sem cards = fim da paginação**.

Armadilhas do HTML VTEX:

1. O span `productBrand` contém o **nome do produto**, não a marca — não há marca no card (deriva-se do título, `config.MARCAS_CONHECIDAS`).
2. O preço vem fatiado em spans e o texto sai espaçado (`R$ 82 , 00`): remover todos os espaços antes de casar o número, senão vira `None` silenciosamente.

### 4.2 Atributos "difíceis" (torra, processo)

Sem página de detalhes: **torra e processo só do título**, senão `NULL`. Regex em `scrapers/extractors.py` (`TORRA_RE`, `PROCESSO_RE`); sem LLM nem enriquecimento automático. Cobertura real é minoria (torra em 4/51 títulos, processo em 1/51 — `tests/test_extractors.py::test_atributos_do_titulo_sao_raros`); os filtros do dashboard operam sobre essa minoria por decisão consciente.

### 4.3 Identificação única do produto

A URL do produto é a chave única (`produtos.url UNIQUE`): estável entre execuções, evita duplicatas mesmo se o título mudar. Idempotência garantida pelo banco.

---

## 5. Política de Limitação de Requisições

Os números vivem em `scrapers/config.py`; esta tabela é o contrato que eles implementam.

| Parâmetro | Valor | Descrição |
|---|---|---|
| `DOWNLOAD_DELAY` | 2 segundos | Intervalo mínimo entre requisições consecutivas |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | 2 | Máximo de 2 requisições simultâneas ao mesmo domínio |
| `MAX_REQUESTS_PER_SCRAPE` | 20 | Teto absoluto por execução |
| `ROBOTS_TXT_OBEY` | True | Respeitar robots.txt; se houver Crawl-delay, usar o maior |
| `USER_AGENT` | `Mozilla/5.0 (compatible; SantaLuziaCafeTracker/2.0; <contato>)` | Contato de `CAFE_CONTATO`, sanitizado para ASCII (acento em cabeçalho HTTP estoura `UnicodeEncodeError`) |
| `TIMEOUT` | 15s | Timeout por requisição |

Erros (constantes em `config.py`, lógica em `scrapers/fetch.py`): **429** → `Retry-After` ou backoff 10s → 20s → 40s, aborta após 2 tentativas; **5xx/timeout** → espera 5s, 1 retry, aborta se persistir.

Não sobreposição de execuções: `concurrency` do workflow (§7.3).

---

## 6. Modelo de Dados (Neon / PostgreSQL)

Schema autoritativo: `scrapers/models.py` (SQLAlchemy), criado de forma idempotente por `models.criar_schema` na primeira execução. Tabelas: `produtos`, `precos`, `coletas` (auditoria de cada execução).

- Datas em **UTC com fuso**; conversão para `America/Sao_Paulo` **apenas na exibição** do dashboard.
- `preco_por_kg` **não é coluna**: calcula-se `preco / peso_g * 1000` no dashboard.
- **Variação (LAG):** calcule o `LAG` numa CTE sobre o histórico completo e só depois filtre o snapshot mais recente — filtrar antes devolve `NULL` em toda a coluna de variação, sem erro. Padrão: `dashboard/dados.py::CONSULTA_ATUAL`; regressão: `tests/test_dashboard_dados.py::test_carregar_produtos_traz_variacao`.

---

## 7. Estrutura, Fluxo de Trabalho e Segurança

### 7.1 Estrutura de diretórios

Árvore anotada: `README.md` §Estrutura.

### 7.2 Desenvolvimento do Scraper

- Parsing: `scrapers/extractors.py` (funções e normalizações documentadas no próprio módulo). Lista vazia de `extrair_cards` é o critério de parada da paginação.
- Persistência (`scrapers/persistencia.py`): produto novo é inserido; existente é atualizado (`ultima_visao`, `ativo=TRUE`); produto ausente da coleta vira `ativo=FALSE` e sai da tabela principal, **mantendo o histórico**. `precos` recebe **uma linha por produto por execução**, sempre.
- **Guarda de sanidade**: aborta **sem snapshot** se a coleta vier abaixo de `GUARDA_MIN_PRODUTOS`, com queda acima de `GUARDA_QUEDA_MAX` vs. coleta anterior, ou acima de `GUARDA_MAX_SEM_PRECO` de cards sem preço (valores em `config.py`). Impede mudança de layout de envenenar o histórico.

### 7.3 Agendamento — GitHub Actions

Coleta **1x/dia às 20h** de Brasília = cron `"0 23 * * *"` UTC em `.github/workflows/scraper.yml` → `python -m scrapers.santa_luzia` (+ `workflow_dispatch` manual).

- **Não sobreposição:** `concurrency: group: coleta-cafes-santa-luzia`, `cancel-in-progress: false` — execução nova espera a anterior. Substitui o lock em arquivo da versão SQLite.
- **Segredos:** `DATABASE_URL` e `CAFE_CONTATO` em `Settings > Secrets and variables > Actions`.
- Runner efêmero, sem estado local; log útil no stdout da run.

### 7.4 Dashboard

- **Somente leitura** por política; `tests/test_dashboard_dados.py::test_dashboard_nao_escreve` varre `dashboard/dados.py` por comandos de escrita.
- KPIs e cards da home olham o **catálogo inteiro**; a sidebar filtra **apenas Tabela e Evolução**.
- "Novidade" = **primeira aparição** (`MIN(precos.data_coleta)`), nunca `produtos.criado_em` (numa migração todos nascem no mesmo dia).
- Produto **sem peso continua visível** (R$/kg em branco): valor desconhecido nunca é "fora da faixa". `dados.formatar_peso` é o único formatador de peso (evita `nang`).
- Evolução: **um produto por vez**, rótulo único via `dados.opcoes_produto` (há homônimos), histórico pelo `produto_id`, **última coleta de cada dia** (`dados.ultima_por_dia`), agregação pela média (`dados.agregar_periodo`).
- Layout, filtros e colunas: `README.md` §Dashboard e `dashboard/app.py`.

### 7.5 Regras de Segurança (obrigatórias)

1. **NUNCA** escrever strings de conexão, tokens ou senhas diretamente no código. Sempre ler de variáveis de ambiente ou `st.secrets`.
2. No código Python:
   - `os.environ["DATABASE_URL"]` para scripts rodando no GitHub Actions (centralizado em `scrapers/base.py`).
   - `st.secrets["DATABASE_URL"]` para código rodando no Streamlit (centralizado em `dashboard/dados.py`).
3. `.streamlit/secrets.toml` deve estar no `.gitignore`. O `secrets.toml.example` (com placeholders) **vai** para o Git.
4. Nos workflows do GitHub Actions, usar `${{ secrets.NOME_DO_SEGREDO }}` para injetar credenciais.
5. Nunca commitar `.env`, `.db`, `.sqlite` ou qualquer arquivo com credenciais.

---

## 8. Comandos

Testes, coleta, diagnóstico e dashboard: `README.md` (§Rodando localmente, §Operação).

---

## 9. Considerações e Riscos

- **Mudanças de layout:** seletores CSS podem quebrar. Ao notar falha, rodar `.venv/bin/pytest` (regressão contra HTML real) e `.venv/bin/python scripts/validar_parser.py`.
- **Como testar se o app responde:** `GET /` devolve `303` para `share.streamlit.io/-/auth/app` — é o bounce que emite a sessão anônima, não parede de login; `curl -I` isolado engana. Use `curl -sL -c /tmp/cj -b /tmp/cj -o /dev/null -w '%{http_code}'` (espera `200`) ou o health `/~/+/_stcore/health` (responde `ok` sem sessão — ver `scripts/keepalive.py`).

---

## 10. Estado do projeto

Em aberto:

- [ ] Acompanhar algumas execuções de `gh run list --workflow=scraper.yml` e a tabela `coletas` para confirmar que a coleta diária segue estável.
- [ ] Triar os itens de melhoria em `docs/plano-melhorias-dashboard-outubro-2026.md`.

---

## 11. Fatos verificados na fonte (13/10/2026)

Recon ao vivo; o essencial também está no docstring de `scrapers/config.py`. Continua válido — a extração não mudou.

- Alvo: categoria `matinais/cafe/cafe-em-graos` (ID 25619), exclusiva de grãos — URL e ID em `config.py` (`LISTAGEM_URL`, `CATEGORIA_ID`). `robots.txt` libera a categoria e as páginas de produto.
- **Não existe atalho JSON:** a API legada (`/api/catalog_system/pub/products/search`) responde HTTP 200 com lista vazia. A extração é por HTML.
- Paginação: 8 itens por página; da página vazia em diante o site responde **HTTP 200 normal, sem cards** — o critério de parada é "página sem cards", não erro.
- Seletores do card: §4.1. Números do dia (51 produtos, faixas de preço): fixtures em `tests/fixtures/`.
