"""Extração dos dados a partir dos CARDS da listagem (AGENTS.md §4).

Regra de ouro: tudo vem da página de listagem. Nenhuma função aqui acessa
página de detalhe de produto.

Recon de 13/10/2026, contra os 51 produtos reais da categoria:
- O PADRAO_TITULO original do AGENTS.md (ancorado, exigindo " - ") falhava em
  38 dos 51 títulos (74%): travessão "–" em vez de hífen, títulos sem separador
  ("Café em Grãos Acaiá Guaspari 250g") e nome com número no meio
  ("Café Em Grãos Nanolote Orgânico Catiguá 1 Lobo do Serrote 250g").
  A regra abaixo — normalizar travessões e tomar o ÚLTIMO token <número><g|kg>
  como peso — extrai peso e nome em 51/51.
- O card expõe um único preço (sellingPrice) e, quando há promoção, o "De"
  riscado (listPrice). Não existe preço de Pix na listagem.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from . import config

# Travessões/hífens tipográficos viram hífen simples antes do parsing.
TRAVESSOES_RE = re.compile(r"\s*[\u2010-\u2015\u2212]\s*")

# O ÚLTIMO <número><unidade> do título é o peso (ver docstring do módulo).
PESO_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g)\b", re.IGNORECASE)

# Torra: aceita "torra clara", "torra média", "torra média-clara", "torra média escura",
# "torra escura" e "torra intensa" (esta última existe na listagem: DuPan).
TORRA_RE = re.compile(
    r"torra\s+(clara|m[ée]dia(?:\s*[-\s]\s*(?:clara|escura))?|escura|intensa)",
    re.IGNORECASE,
)

PROCESSO_RE = re.compile(r"(natural|honey|fermentado|descafeinado|pulped)", re.IGNORECASE)

# "R$ 1.234,56" -> 1234.56
PRECO_BRL_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})")

# Parâmetros de rastreio que não identificam o produto (a URL é a chave única).
PARAMETROS_LIXO = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
                   "gclid", "fbclid", "src", "ref", "_ga"}


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def parse_preco(texto: str | None) -> float | None:
    """Converte o texto de preço do card ("R$ 67,00") em float. None se não houver preço.

    Atenção: a VTEX fatia o preço em spans (moeda, inteiro, decimal), então o texto
    extraído vem espaçado — "R$ 82 , 00". A normalização abaixo remove TODOS os
    espaços antes de casar o padrão; sem ela, o preço vira None silenciosamente.
    """
    if not texto:
        return None
    normalizado = re.sub(r"\s+", "", texto.replace("\xa0", ""))
    m = PRECO_BRL_RE.search(normalizado)
    if not m:
        return None
    return float(m.group(1).replace(".", "") + "." + m.group(2))


def parse_titulo(titulo: str | None) -> tuple[str | None, int | None]:
    """Extrai (nome, peso_g) do título do card.

    peso_g em gramas; "1kg" -> 1000. Retorna None no peso quando não há token de peso.
    """
    if not titulo:
        return None, None
    texto = TRAVESSOES_RE.sub(" - ", titulo).strip()
    pesos = list(PESO_RE.finditer(texto))
    if not pesos:
        return re.sub(r"\s+", " ", texto).strip(" -") or None, None
    ultimo = pesos[-1]
    valor = float(ultimo.group(1).replace(",", "."))
    peso_g = int(round(valor * 1000)) if ultimo.group(2).lower() == "kg" else int(round(valor))
    nome = re.sub(r"\s+", " ", texto[: ultimo.start()]).strip(" -").strip()
    return nome or None, peso_g


def parse_torra(nome: str | None) -> str | None:
    """Torra extraída do título; None quando o título não informa (AGENTS.md §4.2)."""
    if not nome:
        return None
    m = TORRA_RE.search(nome)
    return re.sub(r"\s+", " ", m.group(1)).lower() if m else None


def parse_processo(nome: str | None) -> str | None:
    """Processo extraído do título; None quando o título não informa (AGENTS.md §4.2)."""
    if not nome:
        return None
    m = PROCESSO_RE.search(nome)
    return m.group(1).lower() if m else None


def parse_marca(nome: str | None, marcas: tuple[str, ...] | None = None) -> str | None:
    """Marca derivada do título por casamento com a lista conhecida (config.MARCAS_CONHECIDAS).

    A marca não existe no card. Escolhe o casamento mais longo; None se nada casar.
    """
    if not nome:
        return None
    alvo = _sem_acento(nome)
    achadas = [m for m in (marcas or config.MARCAS_CONHECIDAS) if _sem_acento(m) in alvo]
    if not achadas:
        return None
    return max(achadas, key=len)


def url_canonica(href: str | None, base: str = config.BASE_URL) -> str | None:
    """URL absoluta do produto, sem query/fragmento de rastreio (chave única estável)."""
    if not href:
        return None
    partes = urlsplit(urljoin(base, href))
    query = "&".join(
        p for p in partes.query.split("&") if p and p.split("=")[0].lower() not in PARAMETROS_LIXO
    )
    return urlunsplit((partes.scheme, partes.netloc, partes.path.rstrip("/"), query, ""))


def _texto_do(card, seletor: str) -> str | None:
    """Texto do primeiro elemento que casa com o seletor CSS (None se ausente)."""
    elemento = card.select_one(seletor)
    return elemento.get_text(" ", strip=True) if elemento else None


def extrair_produto(card) -> dict | None:
    """Extrai um card (elemento BeautifulSoup) para dicionário.

    Retorna None se o card não tiver nome (card de propaganda/placeholder).
    """
    titulo = _texto_do(card, '[class*="productName"]') or _texto_do(card, "h3")
    if not titulo:
        return None
    link = card.select_one('a[class*="clearLink"]') or card.select_one("a[href]")
    url = url_canonica(link.get("href") if link else None)
    nome, peso_g = parse_titulo(titulo)
    preco = parse_preco(_texto_do(card, '[class*="sellingPrice"]'))
    preco_original = parse_preco(_texto_do(card, '[class*="listPrice"]'))
    em_promocao = bool(preco_original and preco and preco_original > preco)
    return {
        "titulo": titulo,
        "nome": nome or titulo,
        "url": url,
        "peso_g": peso_g,
        "preco": preco,
        "preco_original": preco_original if em_promocao else None,
        "em_promocao": em_promocao,
        "torra": parse_torra(nome or titulo),
        "processo": parse_processo(nome or titulo),
        "marca": parse_marca(nome or titulo),
    }


def extrair_cards(html: str) -> list[dict]:
    """Extrai todos os cards de produto de uma página de listagem.

    Cada card é um `galleryItem` do resultado de busca da VTEX IO. Página sem
    produto (fim da paginação) devolve lista vazia — é esse o critério de parada.
    """
    soup = BeautifulSoup(html, "lxml")
    produtos = []
    for card in soup.select('[class*="galleryItem"]'):
        produto = extrair_produto(card)
        if produto:
            produtos.append(produto)
    return produtos
