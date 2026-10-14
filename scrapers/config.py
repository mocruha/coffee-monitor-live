"""Configuração central do coletor.

Todos os limites de cortesia do AGENTS.md (§5) vivem aqui, em um único lugar,
para não haver número mágico espalhado pelo código.

Fatos verificados na fonte em 13/10/2026 (ver AGENTS.md §11):
- A categoria "Café em grãos" (ID 25619) tem 51 produtos, 8 por página em 7 páginas.
- Os cards vêm no HTML inicial (VTEX IO server-rendered): Playwright não é necessário.
- O card NÃO expõe preço de Pix: há um único preço (sellingPrice) e, em promoção,
  o "De" riscado (listPrice).
- robots.txt libera a categoria e as páginas de produto.

Persistência: o banco é o Neon (PostgreSQL). A connection string NÃO fica aqui —
vem de `DATABASE_URL` e é resolvida em `scrapers/base.py` (AGENTS.md §2 e §6).
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_env(caminho: Path) -> None:
    """Carrega um .env simples (CHAVE=VALOR) sem trazer dependência extra.

    É só conveniência de desenvolvimento: em produção (GitHub Actions) as
    variáveis vêm do ambiente. `setdefault` garante que o ambiente real vence.
    """
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env(RAIZ / ".env")

# Log em arquivo é opcional: no Actions o log útil é o stdout. Se CAFE_LOG
# estiver definido, também grava em arquivo (útil rodando localmente).
LOG_PATH = Path(os.environ["CAFE_LOG"]) if os.getenv("CAFE_LOG") else None

# --- Alvo ---
BASE_URL = "https://www.santaluzia.com.br"
LISTAGEM_URL = f"{BASE_URL}/matinais/cafe/cafe-em-graos"
CATEGORIA_ID = 25619  # Matinais > Café > Café em grãos
ROBOTS_URL = f"{BASE_URL}/robots.txt"

# --- Política de limitação de requisições (AGENTS.md §5) ---
DOWNLOAD_DELAY = 2.0  # segundos entre requisições consecutivas
CONCURRENT_REQUESTS_PER_DOMAIN = 2  # conexões simultâneas ao mesmo domínio
MAX_REQUESTS_PER_SCRAPE = 20  # teto absoluto por execução
MAX_PAGINAS = 10  # teto de paginação (a categoria pára na 7ª página hoje)
ROBOTS_TXT_OBEY = True
TIMEOUT = 15.0  # segundos por requisição


def _ascii(texto: str) -> str:
    """Cabeçalho HTTP é ASCII: transcreve acentos em vez de estourar UnicodeEncodeError."""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


CONTATO = _ascii(os.getenv("CAFE_CONTATO") or "contato nao configurado")
USER_AGENT = f"Mozilla/5.0 (compatible; SantaLuziaCafeTracker/2.0; {CONTATO})"

# --- Tratamento de erro (AGENTS.md §5) ---
BACKOFF_429 = (10.0, 20.0, 40.0)  # teto de 2 min; depois disso, aborta
RETRY_5XX_DELAY = 5.0
MAX_TENTATIVAS_ERRO = 2

# --- Guarda de sanidade antes de gravar (AGENTS.md §7.2 e §11) ---
# A coleta aborta SEM gravar se o resultado vier suspeito (layout quebrado),
# para não envenenar o histórico com snapshot vazio.
GUARDA_MIN_PRODUTOS = 5
GUARDA_QUEDA_MAX = 0.50  # aborta se perder mais de 50% dos itens da coleta anterior
GUARDA_MAX_SEM_PRECO = 0.10  # aborta se mais de 10% dos cards vierem sem preço

# --- Atributos derivados do título ---
# Marca não existe no card; derivamos do título com esta lista editável.
# A comparação ignora caixa e acentos; quando nada casa, marca fica NULL.
MARCAS_CONHECIDAS = (
    "3 Corações",
    "Baobá",
    "Baggio",
    "Baronesa",
    "Bravo",
    "Chapada de Minas Coffee++",
    "Coffee++",
    "Condessa",
    "Constantino",
    "Duas Irmãs",
    "DuPan",
    "Família Sertão",
    "Guaspari",
    "illy",
    "Latitude 13",
    "Le Mont Spécial",
    "Lobo do Serrote",
    "LP Cafés",
    "Luvizotto",
    "Magalhães",
    "Orfeu",
    "Prima Qualitá",
    "Santa Lúcia",
    "Santa Mônica",
    "Santo Grão",
    "Serra Real",
    "Silvia Magalhães Cafés",
    "Silvia Magalhães",
    "Unique",
    "Zaro Café",
    "Zaro",
)

# Fuso usado apenas na EXIBIÇÃO; o banco grava tudo em UTC.
TZ_EXIBICAO = "America/Sao_Paulo"

# Pesos observados na categoria (150/250/500/1000 g) — usado como sanidade no dashboard.
PESOS_CONHECIDOS = (150, 250, 500, 1000)
