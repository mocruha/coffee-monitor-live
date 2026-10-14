"""Conexão com o banco (Neon / PostgreSQL).

Este é o **único** ponto do sistema que conhece a connection string. A URL
nunca é escrita no código: vem da variável de ambiente `DATABASE_URL`
(AGENTS.md §2 e §6).

- GitHub Actions: injetada de `${{ secrets.DATABASE_URL }}` no workflow.
- Local: `.env` (não versionado) ou exportação direta no shell.

O driver é o `psycopg` (v3). A URL do painel do Neon vem como
`postgresql://...`; aqui ela é normalizada para o dialeto do SQLAlchemy
(`postgresql+psycopg://`) e recebe `sslmode=require` quando o host não é local
(o Neon exige TLS). URLs `sqlite://` passam intactas, o que mantém os testes
herméticos.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

HOSTS_LOCAIS = {"", "localhost", "127.0.0.1", "::1"}


class ConfiguracaoAusente(RuntimeError):
    """`DATABASE_URL` não foi definida."""


def normalizar_url(url: str | None) -> str:
    """Normaliza a connection string para o SQLAlchemy/psycopg, com TLS no Neon.

    - `postgres://` e `postgresql://` viram `postgresql+psycopg://`;
    - hosts remotos ganham `sslmode=require` se ainda não tiverem;
    - qualquer outra URL (ex.: `sqlite:///...` em testes) passa sem alteração.
    """
    texto = (url or "").strip()
    if not texto:
        raise ConfiguracaoAusente(
            "DATABASE_URL não definida. No GitHub Actions, configure o secret "
            "DATABASE_URL; localmente, exporte a variável ou use um arquivo .env."
        )

    if texto.startswith("postgres://"):
        texto = "postgresql+psycopg://" + texto[len("postgres://"):]
    elif texto.startswith("postgresql://"):
        texto = "postgresql+psycopg://" + texto[len("postgresql://"):]

    if not texto.startswith("postgresql+psycopg://"):
        return texto  # sqlite:// etc. — não é Postgres, não mexe

    if "sslmode=" in texto:
        return texto
    if (make_url(texto).host or "") in HOSTS_LOCAIS:
        return texto
    separador = "&" if "?" in texto else "?"
    return f"{texto}{separador}sslmode=require"


def database_url() -> str:
    """Connection string normalizada, lida de `DATABASE_URL`."""
    return normalizar_url(os.environ.get("DATABASE_URL"))


def criar_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Engine do SQLAlchemy para a URL dada (ou para `DATABASE_URL`).

    `pool_pre_ping` porque o Neon suspende conexões ociosas — sem isso, a
    primeira query após ociosidade pode estourar com "server closed the
    connection unexpectedly".
    """
    destino = normalizar_url(url) if url else database_url()
    return create_engine(destino, echo=echo, future=True, pool_pre_ping=True)
