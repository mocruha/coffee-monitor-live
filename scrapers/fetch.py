"""Download da listagem, com a política de cortesia do AGENTS.md (§5).

Este módulo só baixa HTML. O parsing fica em `extractors`, a persistência em
`santa_luzia`/`persistencia`. Nenhuma função aqui acessa página de detalhe.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from urllib import robotparser

import httpx

from . import config

log = logging.getLogger(__name__)


class ColetaAbortada(RuntimeError):
    """Erro irrecuperável: teto de requisições, 429 persistente ou 5xx repetido."""


@dataclass
class EstadoRede:
    """Contadores e relógio da execução (para respeitar delay e teto)."""

    requisicoes: int = 0
    ultima_requisicao: float | None = None
    robots: robotparser.RobotFileParser | None = None
    delay: float = field(default=config.DOWNLOAD_DELAY)

    def pode_requisitar(self) -> bool:
        return self.requisicoes < config.MAX_REQUESTS_PER_SCRAPE


def criar_client() -> httpx.Client:
    """Cliente HTTPX com timeout, UA identificado e limite de conexões."""
    return httpx.Client(
        timeout=httpx.Timeout(config.TIMEOUT),
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9",
        },
        limits=httpx.Limits(
            max_connections=config.CONCURRENT_REQUESTS_PER_DOMAIN,
            max_keepalive_connections=config.CONCURRENT_REQUESTS_PER_DOMAIN,
        ),
        follow_redirects=True,
    )


def carregar_robots(client: httpx.Client, estado: EstadoRede) -> robotparser.RobotFileParser:
    """Baixa e interpreta robots.txt (uma vez por execução)."""
    rp = robotparser.RobotFileParser()
    try:
        resposta = _requisitar(client, config.ROBOTS_URL, estado)
        rp.parse(resposta.text.splitlines())
    except (httpx.HTTPError, ColetaAbortada) as erro:  # robots indisponível não bloqueia
        log.warning("robots.txt indisponível (%s); seguindo com o delay padrão", erro)
        rp.parse([])
    estado.robots = rp
    crawl_delay = rp.crawl_delay(config.USER_AGENT) or rp.crawl_delay("*")
    if crawl_delay and crawl_delay > estado.delay:
        log.info("Crawl-delay do robots.txt (%ss) maior que o delay padrão; usando o maior", crawl_delay)
        estado.delay = float(crawl_delay)
    return rp


def permite(url: str, estado: EstadoRede) -> bool:
    """Consulta o robots.txt já carregado (True se não houver robots ou se permitir)."""
    if not config.ROBOTS_TXT_OBEY:
        return True
    if estado.robots is None:
        return True
    return bool(estado.robots.can_fetch(config.USER_AGENT, url))


def _respeitar_delay(estado: EstadoRede) -> None:
    if estado.ultima_requisicao is None:
        return
    espera = estado.delay - (time.monotonic() - estado.ultima_requisicao)
    if espera > 0:
        time.sleep(espera)


def _requisitar(client: httpx.Client, url: str, estado: EstadoRede) -> httpx.Response:
    """GET cru, contabilizando o teto de requisições e o intervalo mínimo."""
    if not estado.pode_requisitar():
        raise ColetaAbortada(
            f"teto de MAX_REQUESTS_PER_SCRAPE ({config.MAX_REQUESTS_PER_SCRAPE}) atingido"
        )
    _respeitar_delay(estado)
    estado.requisicoes += 1
    estado.ultima_requisicao = time.monotonic()
    return client.get(url)


def baixar(client: httpx.Client, url: str, estado: EstadoRede) -> str:
    """Baixa uma URL respeitando robots.txt, delay, teto, 429 e 5xx.

    - HTTP 429: usa Retry-After se houver; senão backoff 10s -> 20s -> 40s.
      Após MAX_TENTATIVAS_ERRO, aborta a execução.
    - HTTP 5xx e timeout: aguarda RETRY_5XX_DELAY e tenta 1 vez; se persistir, aborta.
    """
    if not permite(url, estado):
        raise ColetaAbortada(f"robots.txt não permite acessar {url}")

    tentativas_429 = 0
    tentativas_5xx = 0

    while True:
        try:
            resposta = _requisitar(client, url, estado)
        except httpx.TimeoutException:
            tentativas_5xx += 1
            log.warning("timeout em %s (tentativa %s)", url, tentativas_5xx)
            if tentativas_5xx > config.MAX_TENTATIVAS_ERRO:
                raise ColetaAbortada(f"timeout persistente em {url}") from None
            time.sleep(config.RETRY_5XX_DELAY)
            continue
        except httpx.HTTPError as erro:
            raise ColetaAbortada(f"falha de rede em {url}: {erro}") from erro

        if resposta.status_code == 429:
            tentativas_429 += 1
            if tentativas_429 > config.MAX_TENTATIVAS_ERRO:
                raise ColetaAbortada(f"HTTP 429 persistente em {url}; coleta abortada")
            espera = _espera_429(resposta, tentativas_429 - 1)
            log.warning("HTTP 429 em %s; aguardando %.0fs", url, espera)
            time.sleep(espera)
            continue

        if 500 <= resposta.status_code < 600:
            tentativas_5xx += 1
            if tentativas_5xx > config.MAX_TENTATIVAS_ERRO:
                raise ColetaAbortada(f"HTTP {resposta.status_code} persistente em {url}")
            log.warning("HTTP %s em %s; aguardando %.0fs", resposta.status_code, url, config.RETRY_5XX_DELAY)
            time.sleep(config.RETRY_5XX_DELAY)
            continue

        resposta.raise_for_status()
        return resposta.text


def _espera_429(resposta: httpx.Response, indice_backoff: int) -> float:
    """Retry-After (segundos) se presente; senão backoff exponencial da config."""
    cabecalho = resposta.headers.get("Retry-After")
    if cabecalho:
        try:
            return min(float(cabecalho.strip()), 120.0)
        except ValueError:
            pass  # Retry-After em formato de data HTTP: cai no backoff
    return config.BACKOFF_429[min(indice_backoff, len(config.BACKOFF_429) - 1)]


def urls_da_listagem(max_paginas: int = config.MAX_PAGINAS) -> list[str]:
    """URLs candidatas da paginação (a parada real é 'página sem cards')."""
    return [
        config.LISTAGEM_URL if pagina == 1 else f"{config.LISTAGEM_URL}?page={pagina}"
        for pagina in range(1, max_paginas + 1)
    ]
