"""Coleta completa dos cafés em grãos e persistência no Neon (PostgreSQL).

Fluxo (AGENTS.md §7.2):

1. baixa a listagem respeitando robots.txt, delay, teto e backoff;
2. aplica a **guarda de sanidade** — se o resultado vier suspeito, aborta SEM gravar;
3. grava um snapshot por produto (`precos`) e um resumo da execução (`coletas`).

Datas são gravadas em UTC com fuso; a conversão para America/Sao_Paulo é do dashboard.

A exclusão mútua entre execuções é o `concurrency` do workflow do GitHub Actions
(AGENTS.md §7.3) — não há lock em arquivo.

Uso:
    python -m scrapers.santa_luzia                 # coleta e grava no banco
    python -m scrapers.santa_luzia --dry-run       # baixa e valida, sem gravar nada
    python -m scrapers.santa_luzia --url sqlite:///local.db   # outro banco (testes)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from . import config, extractors, fetch, models, persistencia

log = logging.getLogger("scraper")

# Códigos de saída (usados pelo agendador para decidir se avisa alguém)
SAIDA_OK = 0
SAIDA_ERRO = 1
SAIDA_ABORTADA = 2


def configurar_log(destino: Path | None = None, verboso: bool = False) -> None:
    destino = destino or config.LOG_PATH
    formato = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG if verboso else logging.INFO)
    if not raiz.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formato)
        raiz.addHandler(console)
        if destino:
            destino.parent.mkdir(parents=True, exist_ok=True)
            arquivo = logging.FileHandler(destino, encoding="utf-8")
            arquivo.setFormatter(formato)
            raiz.addHandler(arquivo)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # o log útil é o nosso


def coletar(paginas_max: int = config.MAX_PAGINAS) -> tuple[dict[str, dict], dict]:
    """Percorre a paginação até a primeira página sem cards. Não toca no banco."""
    estado = fetch.EstadoRede()
    produtos: dict[str, dict] = {}
    paginas_lidas = 0

    with fetch.criar_client() as cliente:
        fetch.carregar_robots(cliente, estado)
        if not fetch.permite(config.LISTAGEM_URL, estado):
            raise fetch.ColetaAbortada("robots.txt não permite acessar a listagem")

        for pagina, url in enumerate(fetch.urls_da_listagem(paginas_max), start=1):
            html = fetch.baixar(cliente, url, estado)
            cards = extractors.extrair_cards(html)
            paginas_lidas += 1
            log.info("página %s: %s cards", pagina, len(cards))
            if not cards:
                break
            for card in cards:
                produtos.setdefault(card["url"], card)

    return produtos, {"paginas": paginas_lidas, "requisicoes": estado.requisicoes, "delay": estado.delay}


def executar(url: str | None = None, paginas_max: int = config.MAX_PAGINAS,
             dry_run: bool = False) -> int:
    iniciada_em = persistencia.agora_utc()
    engine = models.criar_engine(url)
    models.criar_schema(engine)

    try:
        produtos, stats = coletar(paginas_max)
    except fetch.ColetaAbortada as erro:
        log.error("coleta abortada na rede: %s", erro)
        with Session(engine) as session:
            persistencia.registrar_coleta(
                session, iniciada_em=iniciada_em, status="abortada", mensagem=str(erro)
            )
        return SAIDA_ABORTADA

    validos, sem_preco = persistencia.separar_validos(produtos)
    log.info("produtos únicos: %s (%s sem preço)", len(validos), len(sem_preco))

    if dry_run:
        log.info("--dry-run: nada será gravado")
        for nome in sem_preco:
            log.warning("sem preço: %s", nome)
        print(f"\n[dry-run] {len(validos)} produtos válidos, {len(produtos)} cards, "
              f"{stats['paginas']} páginas, {stats['requisicoes']} requisições")
        return SAIDA_OK

    with Session(engine) as session:
        try:
            persistencia.aplicar_guarda(validos, sem_preco, len(produtos), session)
            resultado = persistencia.persistir(session, validos, iniciada_em)
            persistencia.registrar_coleta(
                session, iniciada_em=iniciada_em, status="ok",
                mensagem=f"{len(sem_preco)} cards sem preço" if sem_preco else None,
                paginas=stats["paginas"], requisicoes=stats["requisicoes"],
                vistos=len(produtos), gravados=len(validos),
            )
        except persistencia.GuardaViolada as erro:
            session.rollback()
            persistencia.registrar_coleta(
                session, iniciada_em=iniciada_em, status="abortada",
                mensagem=str(erro), paginas=stats["paginas"],
                requisicoes=stats["requisicoes"], vistos=len(produtos),
            )
            log.error("GRAVAÇÃO ABORTADA: %s", erro)
            return SAIDA_ABORTADA
        except Exception as erro:  # noqa: BLE001 - registra e repropaga
            session.rollback()
            persistencia.registrar_coleta(session, iniciada_em=iniciada_em, status="erro",
                                          mensagem=str(erro))
            log.exception("erro inesperado ao gravar")
            return SAIDA_ERRO

    log.info("coleta ok: %s novos, %s atualizados, %s inativados, %s snapshots",
             resultado["novos"], resultado["atualizados"], resultado["inativos"], len(validos))
    print(f"\ncoleta ok — {len(validos)} produtos gravados "
          f"({resultado['novos']} novos, {resultado['atualizados']} atualizados, "
          f"{resultado['inativos']} inativados) em {stats['paginas']} páginas / "
          f"{stats['requisicoes']} requisições")
    return SAIDA_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Coleta os cafés em grãos da Casa Santa Luzia e grava no Neon (PostgreSQL)."
    )
    parser.add_argument("--url", default=None,
                        help="connection string alternativa (padrão: DATABASE_URL)")
    parser.add_argument("--paginas", type=int, default=config.MAX_PAGINAS, help="teto de páginas")
    parser.add_argument("--dry-run", action="store_true", help="baixa e valida, sem gravar nada")
    parser.add_argument("--log", type=Path, default=None, help="arquivo de log (padrão: só stdout)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    configurar_log(args.log, args.verbose)

    try:
        return executar(args.url, args.paginas, args.dry_run)
    except Exception:  # noqa: BLE001 - falha de configuração/conexão aparece no log
        log.exception("erro fatal")
        return SAIDA_ERRO


if __name__ == "__main__":
    raise SystemExit(main())
