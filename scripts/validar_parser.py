#!/usr/bin/env python3
"""Valida o parser contra a listagem AO VIVO, sem tocar no banco.

Baixa as páginas da categoria "Café em grãos" respeitando robots.txt, delay,
teto de requisições, backoff de 429 e retry de 5xx, extrai os cards e imprime o
resultado.

Não grava nada no banco (nem local nem Neon): é diagnóstico. A gravação é
responsabilidade de `scrapers.santa_luzia`.

Uso:
    python scripts/validar_parser.py
    python scripts/validar_parser.py --json data/validacao.json --csv data/validacao.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers import config, extractors, fetch  # noqa: E402

log = logging.getLogger("validar_parser")


def coletar(paginas_max: int) -> tuple[dict[str, dict], fetch.EstadoRede, int]:
    """Percorre a paginação até a primeira página sem cards. Não persiste nada."""
    estado = fetch.EstadoRede()
    produtos: dict[str, dict] = {}
    paginas_lidas = 0

    with fetch.criar_client() as client:
        fetch.carregar_robots(client, estado)
        if not fetch.permite(config.LISTAGEM_URL, estado):
            raise SystemExit("robots.txt não permite acessar a listagem; abortando.")

        for pagina, url in enumerate(fetch.urls_da_listagem(paginas_max), start=1):
            try:
                html = fetch.baixar(client, url, estado)
            except fetch.ColetaAbortada as erro:
                log.error("abortando na página %s: %s", pagina, erro)
                break
            cards = extractors.extrair_cards(html)
            log.info("página %s: %s cards", pagina, len(cards))
            paginas_lidas += 1
            if not cards:
                break
            for card in cards:
                produtos.setdefault(card["url"], card)

    return produtos, estado, paginas_lidas


def imprimir(produtos: dict[str, dict]) -> None:
    linhas = sorted(produtos.values(), key=lambda c: (c["peso_g"] or 0, c["preco"] or 0))
    print(f"\n{'peso':>6} {'preço':>9} {'R$/kg':>9} {'promo':>6} {'torra':>8} {'processo':>13} {'marca':>22} | produto")
    print("-" * 150)
    for c in linhas:
        por_kg = f"{c['preco'] / c['peso_g'] * 1000:.2f}" if c["preco"] and c["peso_g"] else "-"
        print(
            f"{c['peso_g'] or 0:>5}g {c['preco'] or 0:>9.2f} {por_kg:>9} "
            f"{('SIM' if c['em_promocao'] else ''):>6} {(c['torra'] or '-'):>8} "
            f"{(c['processo'] or '-'):>13} {(c['marca'] or '-'):>22} | {c['nome'][:60]}"
        )

    precos = [c["preco"] for c in produtos.values() if c["preco"]]
    com_marca = sum(1 for c in produtos.values() if c["marca"])
    promo = [c for c in produtos.values() if c["em_promocao"]]
    print("\nresumo")
    print(f"  produtos únicos........: {len(produtos)}")
    print(f"  com preço..............: {len(precos)}/{len(produtos)}")
    print(f"  com peso...............: {sum(1 for c in produtos.values() if c['peso_g'])}/{len(produtos)}")
    print(f"  pesos distintos........: {sorted({c['peso_g'] for c in produtos.values() if c['peso_g']})}")
    if precos:
        print(f"  faixa de preço.........: R$ {min(precos):.2f} - R$ {max(precos):.2f}")
    print(f"  com marca derivada.....: {com_marca}/{len(produtos)}")
    print(f"  torra informada........: {sum(1 for c in produtos.values() if c['torra'])}/{len(produtos)}")
    print(f"  processo informado.....: {sum(1 for c in produtos.values() if c['processo'])}/{len(produtos)}")
    print(f"  em promoção............: {len(promo)}")
    for c in promo:
        print(f"     de R$ {c['preco_original']:.2f} por R$ {c['preco']:.2f} | {c['nome'][:60]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida a extração da listagem (não grava no banco).")
    parser.add_argument("--paginas", type=int, default=config.MAX_PAGINAS)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    produtos, estado, paginas = coletar(args.paginas)
    if not produtos:
        print("nenhum produto extraído — os seletores mudaram?", file=sys.stderr)
        return 1

    imprimir(produtos)
    print(f"\n  páginas lidas..........: {paginas}")
    print(f"  requisições............: {estado.requisicoes} (teto {config.MAX_REQUESTS_PER_SCRAPE})")
    print(f"  intervalo usado........: {estado.delay}s")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(list(produtos.values()), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  JSON...................: {args.json}")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        colunas = ["nome", "marca", "url", "peso_g", "torra", "processo", "preco", "preco_original", "em_promocao"]
        with args.csv.open("w", encoding="utf-8", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=colunas, extrasaction="ignore")
            escritor.writeheader()
            escritor.writerows(produtos.values())
        print(f"  CSV....................: {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
