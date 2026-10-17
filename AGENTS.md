# AGENTS.md — Monitor de Cafés em Grãos (Casa Santa Luzia)

> Documento específico do projeto **Santa Luzia**. A infraestrutura descrita aqui
> segue a arquitetura-alvo de três serviços desacoplados pelo banco (ver §3),
> definida em 13/10/2026. As seções de **extração (§4)** e **limitação de
> requisições (§5)** são independentes da infraestrutura e foram preservadas.

## 1. Missão

Construir e manter um sistema de monitoramento de preços e modelos de **cafés em grãos** vendidos exclusivamente no site do **Casa Santa Luzia** (`santaluzia.com.br`). O sistema deve:

- Coletar dados **uma vez por dia** (20h, horário de Brasília).
- **Extrair tudo a partir da página de listagem** (cards de produto), sem acessar páginas de detalhes.
- Armazenar histórico de preços para calcular variações.
- Disponibilizar um **dashboard web** com filtros por peso, marca, preço, R$/kg e promoção.

---

## 2. Stack Tecnológica

A stack é composta por **três serviços independentes que se comunicam apenas
através do banco de dados PostgreSQL (Neon)**.

### 2.1 Scraper (Coleta) — GitHub Actions

| Componente | Escolha | Justificativa |
|---|---|---|
| Linguagem | **Python 3.12** | Ecossistema maduro, integração natural com o dashboard. |
| HTTP Client | **HTTPX** | Suporte a async e timeouts, mais moderno que `requests`. |
| Parser HTML | **BeautifulSoup4 + lxml** | Suficiente, pois só precisamos ler a listagem. |
| Renderização JS | **Playwright** (descartado) | Verificado em 13/10/2026: os cards vêm no HTML inicial. Não é dependência. |
| Agendamento | **GitHub Actions** (cron) | Agenda persistente, sem depender do PC ligado. |
| Execução | **sem estado local** | Cada run é efêmero; não persiste arquivos entre execuções. |

### 2.2 Persistência

| Componente | Escolha |
|---|---|
| Banco | **Neon (PostgreSQL)** — única persistência do sistema |
| Driver | **psycopg 3** (`psycopg[binary]`) |
| ORM | **SQLAlchemy 2.x** |

A connection string é um **segredo** (`DATABASE_URL`) e nunca aparece no código.

### 2.3 Dashboard Web — Streamlit Community Cloud

| Componente | Escolha |
|---|---|
| Framework | **Streamlit** |
| Gráficos | **Plotly** |
| Tabela interativa | Streamlit nativo + Plotly |
| Acesso | Controlado pelas configurações de privacidade do Streamlit Cloud (lista de e-mails) |
| Escrita | **Nenhuma** — o painel só lê o Neon (§7.4) |

### 2.4 Ambiente de desenvolvimento

- Python 3.12 e um `.venv` local. Sem Playwright, sem Airflow, sem APScheduler.
- Testes rodam offline em SQLite; o CI roda a mesma suíte contra PostgreSQL.

---

## 3. Arquitetura do Sistema

Três serviços, acoplados **apenas pelo banco**:

```text
┌──────────────────────────────────────┐
│ SCRAPER — GitHub Actions (cron)      │
│ Cron: 0 23 * * * (UTC)               │
│      = 20h America/Sao_Paulo         │
│ 1. Acessa a listagem de cafés        │
│    (com paginação)                   │
│ 2. Extrai cada card (§4)             │
│ 3. NÃO acessa páginas de detalhe     │
│ 4. Normaliza e faz INSERT/UPDATE     │
│    no Neon                           │
│ Sem estado local. Sem arquivos.      │
└──────────────────────────────────────┘
                    │  escreve
                    ▼
┌──────────────────────────────────────┐
│ BANCO — Neon (PostgreSQL)            │
│ produtos, precos, coletas            │
│ Única persistência. Segredo:         │
│ DATABASE_URL.                        │
└──────────────────────────────────────┘
                    │  lê
                    ▼
┌──────────────────────────────────────┐
│ DASHBOARD — Streamlit Cloud          │
│  - Filtros: peso, preço, R$/kg,      │
│    promoção, marca                   │
│  - Tabela com preço atual e variação │
│  - Evolução de preço por produto     │
│  - Destaques e promoções ativas      │
│ Somente leitura. Acesso por e-mail.  │
└──────────────────────────────────────┘
```

**Consequência da separação:** não existe mais lock em arquivo nem estado
compartilhado. A exclusão mútua entre execuções é o `concurrency` do workflow
(§7.3) e a idempotência vem da URL única do produto (§4.3).

---

## 4. Estratégia de Extração (somente listagem)

> Seção preservada da especificação original: descreve o alvo, não a infraestrutura.

### 4.1 Fontes de dados disponíveis no card

Toda a extração vem de **duas fontes dentro do card de produto**:

**Fonte A — Título (padrão conhecido):**

```text
Café <nome> - <peso>g
```

Exemplos:

- `Café Santa Luzia Torra Média - 250g`
- `Café Orfeu Intenso - 500g`
- `Café 3 Corações Rituais - 1kg`

Parsing do título (regra revisada em 13/10/2026, contra os 51 títulos reais — implementada em `scrapers/extractors.py`):

1. Normalizar travessões e hífens tipográficos (`–`, `—`, `−`) para `-`.
2. Tomar o **último** token `<número><g|kg>` do título como peso (a unidade é obrigatória, então números que fazem parte do nome não confundem o parser).
3. O nome é tudo o que vem antes desse token, sem separador nas pontas.

Conversão:

- `g` → valor direto
- `kg` → valor × 1000

Motivo da revisão: o regex ancorado original,

```python
PADRAO_TITULO = re.compile(
    r"^Café\s+(?P<nome>.+?)\s*-\s*(?P<peso>\d+)\s*(?P<unidade>g|kg)$",
    re.IGNORECASE
)
```

falhava em 38 dos 51 títulos reais (74%): travessão unicode (`Café Coletânea Torra Clara em Grãos Bravo – 250g`), título sem separador (`Café em Grãos Acaiá Guaspari 250g`) e nome com número no meio (`Café Em Grãos Nanolote Orgânico Catiguá 1 Lobo do Serrote 250g`). A regra acima extrai peso e nome em 51/51 — garantido pelo teste `tests/test_extractors.py::test_regex_original_do_agents_md_falhava`.

**Fonte B — Tags/campos do card (confirmados no HTML real):**

- Preço atual: span com classe contendo `sellingPrice`. É o **único** preço exposto — **não existe preço de Pix na listagem** (verificado em 13/10/2026; as ocorrências de "Pix" no HTML eram do pixel do Facebook).
- Preço original: span com classe contendo `listPrice`, presente apenas quando há promoção (o "De" riscado). Logo `em_promocao = listPrice > sellingPrice`.
- URL do produto: `a.clearLink` — chave única, absoluta e sem parâmetros de rastreio.
- Nome: span com classe contendo `productName`.
- Card: elemento `[class*="galleryItem"]`; página sem cards = fim da paginação.

Duas armadilhas do HTML, já tratadas em `scrapers/extractors.py`:

1. Existe um span `productBrand` no card, mas ele contém o **nome do produto** — não a marca. **Não há marca no card.**
2. A VTEX fatura o preço em vários spans (moeda, inteiro, decimal), então o texto extraído vem espaçado: `R$ 82 , 00`. É preciso remover todos os espaços antes de casar o número, senão o preço vira `None` silenciosamente.

### 4.2 Atributos "difíceis" (torra, processo) — decisão revisada

Não acessar a página de detalhes. Portanto:

**Torra**: extrair apenas se aparecer no título. Regex aplicada ao nome:

```python
TORRA_RE = re.compile(
    r"torra\s+(clara|m[ée]dia(?:\s*[-\s]\s*(?:clara|escura))?|escura|intensa)",
    re.IGNORECASE,
)
```

`intensa` entrou na lista depois da recon (aparece em "Café Grão Torra Intensa DuPan"). Se não aparecer no título → `torra = NULL`.

**Cobertura real em 13/10/2026: torra em 4 dos 51 títulos e processo em 1 (`descafeinado`).** Na prática, os filtros de torra e processo do dashboard operam sobre uma minoria dos produtos — consequência consciente de não acessar páginas de detalhe.

**Processo** (Natural, Honey, Fermentado, Descafeinado): mesma lógica. Muitos produtos incluem "Natural", "Fermentado", "Descafeinado" no nome. Regex:

```python
PROCESSO_RE = re.compile(r"(natural|honey|fermentado|descafeinado|pulped)", re.IGNORECASE)
```

Se não aparecer no título → `processo = NULL`.

Justificativa: manter o scraper simples e de alto desempenho, sem depender de descrições longas ou LLM. Produtos com atributos faltantes ficam como NULL no banco e podem ser enriquecidos manualmente depois, se valer a pena.

### 4.3 Identificação única do produto

Usar a URL do produto como chave única. Isso é estável entre execuções e evita duplicatas mesmo se o título mudar levemente. No Postgres, a coluna `produtos.url` tem `UNIQUE`, então a idempotência é garantida pelo banco.

---

## 5. Política de Limitação de Requisições

> Seção preservada da especificação original. Os números vivem em `scrapers/config.py`.

Como agora só acessamos a página de listagem (provavelmente 1–5 páginas), o volume é mínimo. Mesmo assim, manter regras estritas:

| Parâmetro | Valor | Descrição |
|---|---|---|
| `DOWNLOAD_DELAY` | 2 segundos | Intervalo mínimo entre requisições consecutivas |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | 2 | Máximo de 2 requisições simultâneas ao mesmo domínio |
| `MAX_REQUESTS_PER_SCRAPE` | 20 | Teto absoluto por execução (listagem + eventuais páginas) |
| `ROBOTS_TXT_OBEY` | True | Respeitar robots.txt; se houver Crawl-delay, usar o maior |
| `USER_AGENT` | `Mozilla/5.0 (compatible; SantaLuziaCafeTracker/2.0; <contato>)` | Identificação clara. O contato vem de `CAFE_CONTATO` e é sanitizado para ASCII — acento em cabeçalho HTTP estoura `UnicodeEncodeError` |
| `TIMEOUT` | 15s | Timeout por requisição |

Volume real medido em 13/10/2026: 7 páginas com produtos + 1 página vazia (que encerra a paginação) + 1 robots.txt = **9 requisições por execução**, ~18s de duração com o delay de 2s. Muito abaixo do teto. Com a coleta agora **1x/dia**, são ~9 requisições/dia.

Tratamento de erros:

- **HTTP 429 (Too Many Requests):**
  - Ler `Retry-After` se presente.
  - Senão, backoff exponencial: 10s → 20s → 40s (máx. 2 min).
  - Após 2 tentativas, abortar a execução e registrar log.
- **HTTP 5xx:** aguardar 5s, tentar 1 vez. Se persistir, abortar.
- **Timeout:** tratar como 5xx (1 retry).

Não sobreposição de execuções: `concurrency` do workflow do GitHub Actions (§7.3).

---

## 6. Modelo de Dados (Neon / PostgreSQL)

O banco começa **vazio** (a migração optou por não trazer o histórico SQLite —
ver §10). O schema é criado de forma idempotente por `models.criar_schema`
(`Base.metadata.create_all`) na primeira execução do scraper.

```sql
-- Datas em UTC com fuso (TIMESTAMP WITH TIME ZONE). A conversão para
-- America/Sao_Paulo é feita APENAS na exibição do dashboard.

CREATE TABLE IF NOT EXISTS produtos (
    id            SERIAL PRIMARY KEY,
    nome          TEXT NOT NULL,
    marca         TEXT,               -- derivada do título; NULL quando não identificada
    url           TEXT UNIQUE NOT NULL, -- chave única, sem parâmetros de rastreio
    peso_g        INTEGER,
    torra         TEXT,               -- apenas se aparecer no título
    processo      TEXT,               -- apenas se aparecer no título
    ativo         BOOLEAN NOT NULL DEFAULT TRUE, -- FALSE quando o produto sai da listagem
    ultima_visao  TIMESTAMP WITH TIME ZONE,      -- última coleta em que o produto apareceu
    criado_em     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS precos (
    id             SERIAL PRIMARY KEY,
    produto_id     INTEGER NOT NULL REFERENCES produtos(id),
    preco          DOUBLE PRECISION NOT NULL,
    preco_original DOUBLE PRECISION,   -- se em promoção
    em_promocao    BOOLEAN NOT NULL DEFAULT FALSE,
    data_coleta    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_precos_produto_data ON precos(produto_id, data_coleta DESC);

-- Auditoria de cada execução: detecta coleta vazia ou layout quebrado.
CREATE TABLE IF NOT EXISTS coletas (
    id              SERIAL PRIMARY KEY,
    iniciada_em     TIMESTAMP WITH TIME ZONE NOT NULL,
    finalizada_em   TIMESTAMP WITH TIME ZONE,
    paginas         INTEGER NOT NULL DEFAULT 0,
    requisicoes     INTEGER NOT NULL DEFAULT 0,
    itens_vistos    INTEGER NOT NULL DEFAULT 0,
    itens_gravados  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'em_andamento',
    mensagem        TEXT
);
```

`preco_por_kg` **não** é coluna do banco: é calculado no dashboard (`preco / peso_g * 1000`), porque comparar 250g com 1kg sem normalizar engana.

> **Atenção ao filtrar por coleta (verificado em 13/10/2026):** uma janela de datas mantém
> todos os snapshots, então `LAG` funciona. Se o dashboard filtrar *apenas a coleta mais recente*
> (`data_coleta = max(data_coleta)`) na mesma consulta, o `LAG` passa a ver uma linha por produto
> e devolve `NULL` em toda a coluna de variação. Calcule o `LAG` numa CTE sobre o histórico
> completo e só depois filtre. É o que `dashboard/dados.py::CONSULTA_ATUAL` faz (e há teste
> de regressão: `tests/test_dashboard_dados.py::test_carregar_produtos_traz_variacao`).

---

## 7. Estrutura, Fluxo de Trabalho e Segurança

### 7.1 Estrutura de diretórios

```text
coffee-monitor-live/
├── .github/
│   └── workflows/
│       ├── scraper.yml           # coleta 1x/dia + dispatch manual
│       └── testes.yml            # pytest com service container PostgreSQL
├── .streamlit/
│   ├── secrets.toml              # NUNCA vai para o Git (no .gitignore)
│   └── secrets.toml.example      # placeholders, vai para o Git
├── scrapers/
│   ├── __init__.py
│   ├── base.py                   # conexão com o Neon (única fonte de DATABASE_URL)
│   ├── config.py                 # alvo, limites do §5, marcas conhecidas
│   ├── fetch.py                  # cliente HTTPX + robots.txt + delay + backoff
│   ├── extractors.py             # parsing dos cards (§4)
│   ├── models.py                 # SQLAlchemy: produtos, precos, coletas
│   ├── persistencia.py           # guarda de sanidade + upsert + auditoria
│   └── santa_luzia.py            # entry point: coleta e grava  (python -m scrapers.santa_luzia)
├── dashboard/
│   ├── __init__.py
│   ├── app.py                    # entry point do Streamlit
│   └── dados.py                  # consultas de leitura
├── scripts/
│   └── validar_parser.py         # diagnóstico ao vivo, sem gravar
├── tests/
│   ├── conftest.py               # engine por teste (SQLite local / Postgres no CI)
│   ├── test_extractors.py        # parsing e regressão de layout
│   ├── test_persistencia.py      # gravação, guarda de sanidade, auditoria
│   ├── test_dashboard_dados.py   # variação (LAG), somente leitura, resumo
│   ├── test_santa_luzia.py       # integração offline do entry point
│   └── fixtures/                 # 8 páginas reais + snapshot dos 51 produtos
├── .env.example
├── .gitignore
├── requirements.txt              # runtime (scraper + dashboard)
├── requirements-dev.txt          # pytest
├── pytest.ini
├── README.md
└── AGENTS.md
```

### 7.2 Desenvolvimento do Scraper

- **Extractors** (`scrapers/extractors.py`):
  - `extrair_cards(html)` → lista de cards (dicts) de uma página; lista vazia é o critério de parada da paginação.
  - `parse_titulo(titulo)` → `(nome, peso_g)` pela regra revisada da §4.1.
  - `parse_preco(texto)` → float a partir do texto fatiado em spans.
  - `parse_torra(nome)` / `parse_processo(nome)` → do título, ou `None`.
  - `parse_marca(nome)` → marca por lista editável (`config.MARCAS_CONHECIDAS`), ou `None`.
  - `url_canonica(href)` → URL absoluta sem parâmetros de rastreio (chave única).
  - `extrair_produto(card)` → dicionário completo de um card.

- **Normalização**:
  - Peso: `g` direto; `kg` × 1000.
  - Preço: remover espaços (a VTEX fatia o preço em spans) e trocar `,` por `.`.
  - Promoção: `em_promocao = (preco_original is not None and preco_original > preco)`.
  - Marca: casamento mais longo contra a lista conhecida, ignorando caixa e acentos.

- **Persistência** (`scrapers/persistencia.py`): produto novo é inserido; produto existente é atualizado (nome, marca, peso, torra, processo, `ultima_visao`, `ativo=TRUE`); produto que não veio na coleta vira `ativo=FALSE` e sai da tabela principal, mantendo o histórico. `precos` recebe uma linha por produto por execução, sempre.

- **Guarda de sanidade**: antes de gravar, aborta **sem snapshot** se a coleta vier com menos de `GUARDA_MIN_PRODUTOS` (5) itens, com queda acima de `GUARDA_QUEDA_MAX` (50%) em relação à coleta anterior, ou com mais de `GUARDA_MAX_SEM_PRECO` (10%) de cards sem preço — é o que impede uma mudança de layout de envenenar o histórico.

### 7.3 Agendamento — GitHub Actions

**Decisão (13/10/2026, revisada em 16/10/2026):** a coleta roda no **GitHub
Actions**, **1x/dia às 20h** (antes eram 2x/dia, às 08h e 20h), abandonando
o cronjob local do Hermes. O cron do Actions é em **UTC**:

```yaml
on:
  schedule:
    - cron: "0 23 * * *"      # 23h UTC = 20h em America/Sao_Paulo (UTC-3)
  workflow_dispatch: {}        # disparo manual pela aba Actions
```

- **Workflow:** `.github/workflows/scraper.yml` → `python -m scrapers.santa_luzia`.
- **Segredos:** `DATABASE_URL` e `CAFE_CONTATO` vêm de
  `Settings > Secrets and variables > Actions`.
- **Não sobreposição:** `concurrency: group: coleta-cafes-santa-luzia` com
  `cancel-in-progress: false` — uma execução nova espera a anterior, em vez de
  rodar em paralelo. **Isto substitui o lock em arquivo** da versão SQLite.
- **Sem estado local:** o runner é efêmero; nada é gravado além do Neon. Logs
  úteis vão para o stdout da run.
- **Atrasos:** o cron do Actions pode atrasar alguns minutos em horários de pico;
  aceitável para 1 coleta/dia.
- **Justificativa vs. alternativas:** cronjob local (WSL) só roda com o PC
  ligado; APScheduler seria mais uma dependência. O Actions tem agenda
  persistente e é gratuito para repositórios públicos.

### 7.4 Dashboard

- Carregar dados com pandas + cache `@st.cache_data(ttl=3600)`.
- Conexão via `st.secrets["DATABASE_URL"]` (fallback para a variável de ambiente).
- **Somente leitura:** o painel nunca escreve — a garantia é de política, e há um
  teste que varre `dashboard/dados.py` por comandos de escrita
  (`test_dashboard_nao_escreve`).
- Filtros na sidebar: peso, marca, faixa de preço, faixa de R$/kg, "somente promoção" e busca por nome.
- Tabela principal (apenas produtos na listagem; os inativos ficam no banco, com histórico): nome, marca, peso, preço atual, **preço por kg** (`preco / peso_g * 1000`), variação % contra a coleta anterior (↑/↓) e badge de promoção. Ordenação por um dropdown (Variação, Preço, R$/kg, Peso, Nome) + seletor ascendente/descendente.
- Gráfico de linha: evolução do preço de **um produto por vez**, com granularidade escolhida (dia, semana ou mês) e agregação pela média do período. Usa a **última coleta de cada dia** (`dados.ultima_por_dia`) — no histórico antigo isso é a das 20h; as coletas das 08h ficam no banco mas não entram no gráfico. A agregação vive em `dados.agregar_periodo`.
- Destaques: maiores quedas, promoções ativas e menor R$/kg.

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

| Comando | Ação |
|---|---|
| `.venv/bin/pytest` | Roda os testes offline (SQLite) contra o HTML real salvo em `tests/fixtures/`. |
| `TEST_DATABASE_URL=postgresql+psycopg://... .venv/bin/pytest` | Roda a mesma suíte contra um PostgreSQL real. |
| `CAFE_LIVE=1 .venv/bin/pytest -k ao_vivo` | Valida o parser contra o site ao vivo. |
| `.venv/bin/python scripts/validar_parser.py` | Diagnóstico do parser: baixa a listagem, imprime os produtos e **não grava no banco**. |
| `.venv/bin/python -m scrapers.santa_luzia --dry-run` | Baixa e valida, sem gravar nada. |
| `.venv/bin/python -m scrapers.santa_luzia` | Executa a coleta e persiste no Neon (usando `DATABASE_URL`). |
| `.venv/bin/streamlit run dashboard/app.py` | Sobe o dashboard localmente. |
| `gh workflow run scraper.yml` | Dispara a coleta manualmente no GitHub Actions. |
| `gh run list --workflow=scraper.yml` | Histórico das execuções agendadas. |

> Os comandos `hermes init/scrape/schedule/dashboard/test-extract/db-migrate` citados em
> versões anteriores deste documento nunca existiram e foram removidos junto com o
> agendador do Hermes.

---

## 9. Considerações e Riscos

- **Cards via JS — resolvido**: verificado em 13/10/2026 que a listagem vem no HTML inicial (VTEX IO server-rendered). Playwright fica fora do projeto.
- **Atributos não presentes no título**: torra e processo ficarão NULL para vários produtos. Isso é aceitável — o dashboard mostra "não informado" e os filtros ignoram NULL.
- **Mudanças de layout**: seletores CSS podem quebrar. Manter o código modular e rodar `.venv/bin/pytest` (testes de regressão contra o HTML real) e `scripts/validar_parser.py` sempre que notar falha.
- **Anti-bot**: com 1 execução/dia e intervalo de 2s, risco mínimo.
- **Idempotência**: URL como chave única (`UNIQUE`) evita duplicação de produtos; `precos` sempre insere nova linha (histórico).
- **Neon suspende conexões ociosas**: por isso o engine usa `pool_pre_ping=True` (`scrapers/base.py`).
- **Segredo no Streamlit Cloud**: o acesso é restrito por lista de e-mails; qualquer pessoa autorizada enxerga os dados, mas não as credenciais.
- **Sem Airflow nesta fase**: GitHub Actions é suficiente para 1 execução diária sem dependências complexas.

---

## 10. Próximos Passos — situação

### Migração de stack (13/10/2026)

- [x] Reescrever o `AGENTS.md` para a arquitetura de 3 serviços.
- [x] Estrutura de diretórios + arquivos de configuração (`requirements`, `.gitignore`, `secrets.toml.example`, workflows).
- [x] `scrapers/base.py`: conexão com o Neon via `DATABASE_URL` (fonte única).
- [x] Portar o scraper: `scrapers/{config,fetch,extractors,models,persistencia,santa_luzia}.py`.
- [x] Modelo de dados em PostgreSQL (`TIMESTAMPTZ`, `SERIAL`, `now()`), começando vazio.
- [x] Dashboard lendo do Neon via `st.secrets`/`DATABASE_URL`.
- [x] Suíte de testes portada e verde (65 testes + 1 de rede opt-in), com service container PostgreSQL no CI.

### Pendências (feitas fora do código, no painel/GitHub)

- [x] Criar o projeto no **Neon** (PostgreSQL) e copiar a connection string.
- [x] Cadastrar os secrets do repositório: `DATABASE_URL` e `CAFE_CONTATO`.
- [x] Criar o repositório no GitHub e dar push — **privado**, `mocruha/coffee-monitor-live`,
      branch `main`, CI de *Testes* verde (pytest contra Postgres de serviço).
- [x] Publicar o app no **Streamlit Community Cloud** — `https://coffee-monitor-live.streamlit.app/`
      (privado, herda a privacidade do repo). Entrypoint `dashboard/app.py`, Python 3.12, secret
      `DATABASE_URL`. Confirmado lendo o Neon: 51 produtos ativos, 2 promoções, coleta `ok`.
- [x] Disparar `gh workflow run scraper.yml` uma vez e conferir a primeira coleta no Neon —
      run `38511612477` verde: 8 páginas, **51 produtos gravados** (51 novos, 0 sem preço), coleta `ok`.
- [x] **Desativar o cronjob do Hermes** `ba8ee65f81bf` (`coleta-cafes-santa-luzia`) — removido em
      13/10/2026 com `hermes cron remove`; `hermes cron list` não tem mais jobs. O projeto antigo em
      `/home/moc/hermes/coffee-monitor` (SQLite) permanece no disco, mas sem agendamento.
- [ ] Monitorar 2–3 dias: conferir `gh run list --workflow=scraper.yml` e a tabela `coletas`.

---

## 11. Fatos verificados na fonte (recon de 13/10/2026)

Levantamento feito direto no site, com o parser rodando ao vivo. É a base de todas as
correções aplicadas neste documento. **Continua válido** — a extração não mudou.

### 11.1 Alvo e permissão

- Categoria correta: `https://www.santaluzia.com.br/matinais/cafe/cafe-em-graos` (ID 25619,
  Matinais > Café > Café em grãos). Ela é separada de `cafe-torrado-e-moido`,
  `cafe-em-capsulas`, `cafe-soluvel`, `drip-coffe` e `sache` — o escopo "só grãos" tem
  categoria própria e não precisa de heurística no título.
- `robots.txt` libera a categoria e as páginas de produto. Bloqueia `/account`, `/login`,
  `/checkout`, `/busca`, `/espiar`, `/admin` e `/api/sessions` — nada que o projeto use.
- A loja é VTEX IO. A API legada de catálogo (`/api/catalog_system/pub/products/search`)
  responde HTTP 200 com lista vazia: não existe atalho JSON, a extração é por HTML.

### 11.2 Volume e paginação

- 51 produtos em 8 páginas: 7 páginas com 8 itens, a última com 3 (8 por página).
- Da 8ª página em diante o site responde **HTTP 200 com uma página normal, porém sem cards** —
  por isso o critério de parada é "página sem cards", e não "página com erro".
- 9 requisições por execução (7 páginas + a vazia + robots.txt), ~18s com o delay de 2s.

### 11.3 O que o card oferece

| Dado | Onde está | Observação |
|---|---|---|
| Nome | span `[class*="productName"]` | título completo |
| Preço atual | span `[class*="sellingPrice"]` | único preço do card |
| Preço original | span `[class*="listPrice"]` | existe apenas em promoção |
| URL | `a[class*="clearLink"]` | chave única do produto |
| Card | `[class*="galleryItem"]` | 8 por página |

Não existem no card: marca, torra, processo, badge de promoção nem preço de Pix. O span
chamado `productBrand` contém o **nome do produto** — nome enganoso da VTEX.

### 11.4 Números de referência do dia

- Preços: R$ 29,00 (menor) a R$ 231,00 (maior).
- Preço por quilo: de R$ 116,00 a R$ 924,00 no mesmo instante — comparar pesos diferentes
  sem normalizar por quilo engana.
- Promoções: 2 de 51 (`Café Rituais Grão 3 Corações - 500g`, R$ 93,00 → R$ 67,00;
  `Café torrado em grãos illy 250g`, R$ 70,00 → R$ 59,90).
- Atributos do título: torra em 4 títulos, processo em 1; marca derivada em 48 de 51
  (as 3 restantes ficam NULL por não terem marca reconhecível no título).
