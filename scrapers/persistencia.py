"""Persistência do snapshot e guarda de sanidade (AGENTS.md §6 e §7.2).

Separado do entry point (`santa_luzia.py`) para que a lógica de gravação seja
testável sem rede e reutilizável por qualquer coleta.

Diferença em relação à versão SQLite: **não há lock em arquivo**. No GitHub
Actions não existe filesystem compartilhado entre execuções; a exclusão mútua
passa a ser o `concurrency` do workflow, e a URL única do produto garante a
idempotência.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, models

log = logging.getLogger("scraper")


class GuardaViolada(RuntimeError):
    """A coleta veio suspeita: nada foi gravado."""


def agora_utc() -> datetime:
    """UTC com fuso (o banco usa TIMESTAMP WITH TIME ZONE)."""
    return datetime.now(timezone.utc)


def separar_validos(produtos: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """Separa cards utilizáveis dos sem preço (preço ausente = seletor quebrado)."""
    validos = {u: c for u, c in produtos.items() if c["preco"] is not None and c["url"]}
    sem_preco = [c["nome"] for c in produtos.values() if c["preco"] is None or not c["url"]]
    return validos, sem_preco


def aplicar_guarda(validos: dict[str, dict], sem_preco: list[str], produtos_vistos: int,
                   session: Session) -> None:
    """Aborta a gravação se a coleta vier suspeita (AGENTS.md §7.2).

    Três condições, todas capazes de indicar layout quebrado:
    1. menos de GUARDA_MIN_PRODUTOS produtos;
    2. queda acima de GUARDA_QUEDA_MAX em relação aos produtos ativos da coleta anterior;
    3. mais de GUARDA_MAX_SEM_PRECO dos cards sem preço (seletor de preço quebrado).
    """
    total = len(validos)
    if total < config.GUARDA_MIN_PRODUTOS:
        raise GuardaViolada(
            f"apenas {total} produtos válidos (mínimo {config.GUARDA_MIN_PRODUTOS}); nada foi gravado"
        )

    if produtos_vistos and sem_preco:
        fracao = len(sem_preco) / produtos_vistos
        if fracao > config.GUARDA_MAX_SEM_PRECO:
            raise GuardaViolada(
                f"{len(sem_preco)} de {produtos_vistos} cards sem preço "
                f"({fracao:.0%} > {config.GUARDA_MAX_SEM_PRECO:.0%}); nada foi gravado"
            )

    ativos_antes = session.scalar(
        select(func.count()).select_from(models.Produto).where(models.Produto.ativo.is_(True))
    ) or 0
    if ativos_antes:
        queda = 1 - total / ativos_antes
        if queda > config.GUARDA_QUEDA_MAX:
            raise GuardaViolada(
                f"queda de {queda:.0%} nos itens ({ativos_antes} ativos -> {total}); nada foi gravado"
            )
    log.info(
        "guarda de sanidade ok: %s produtos válidos, %s ativos antes, %s sem preço",
        total, ativos_antes, len(sem_preco),
    )


def persistir(session: Session, validos: dict[str, dict], momento: datetime) -> dict:
    """Grava produtos novos/atualizados, um snapshot de preço por produto e desativa ausentes."""
    novos = atualizados = 0
    pares: list[tuple[models.Produto, dict]] = []

    # Passada 1: upsert dos produtos. Nada de flush antes de preencher os campos —
    # `nome` é NOT NULL e um flush prematuro gravaria NULL.
    for card in validos.values():
        produto = session.scalar(select(models.Produto).where(models.Produto.url == card["url"]))
        if produto is None:
            produto = models.Produto(url=card["url"])
            session.add(produto)
            novos += 1
        else:
            atualizados += 1

        produto.nome = card["nome"]
        produto.marca = card["marca"]
        produto.peso_g = card["peso_g"]
        produto.torra = card["torra"]
        produto.processo = card["processo"]
        produto.ativo = True
        produto.ultima_visao = momento
        pares.append((produto, card))

    session.flush()  # aqui os ids já existem para amarrar os snapshots

    # Passada 2: um snapshot por produto e desativação de quem não apareceu.
    ids_vistos = set()
    for produto, card in pares:
        session.add(
            models.Preco(
                produto_id=produto.id,
                preco=card["preco"],
                preco_original=card["preco_original"],
                em_promocao=card["em_promocao"],
                data_coleta=momento,
            )
        )
        ids_vistos.add(produto.id)

    ausentes = session.scalars(
        select(models.Produto).where(
            models.Produto.ativo.is_(True), models.Produto.id.notin_(ids_vistos)
        )
    ).all()
    for produto in ausentes:
        produto.ativo = False

    session.flush()
    return {"novos": novos, "atualizados": atualizados, "inativos": len(ausentes)}


def registrar_coleta(session: Session, *, iniciada_em: datetime, status: str,
                     mensagem: str | None = None, paginas: int = 0, requisicoes: int = 0,
                     vistos: int = 0, gravados: int = 0) -> None:
    """Grava o resumo da execução (auditoria) e confirma a transação."""
    session.add(
        models.Coleta(
            iniciada_em=iniciada_em,
            finalizada_em=agora_utc(),
            paginas=paginas,
            requisicoes=requisicoes,
            itens_vistos=vistos,
            itens_gravados=gravados,
            status=status,
            mensagem=mensagem,
        )
    )
    session.commit()
