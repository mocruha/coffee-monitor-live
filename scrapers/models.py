"""Modelo de dados (SQLAlchemy 2.x) sobre PostgreSQL (Neon).

Diferenças em relação ao schema original do AGENTS.md (§6), aprovadas na revisão:
- `produtos.ativo` + `produtos.ultima_visao`: produto que sai da listagem fica
  inativo, sai da tabela principal e mantém todo o histórico de preços.
- `coletas`: uma linha por execução, para auditar falhas e coletas vazias.
- Datas em UTC com fuso (`TIMESTAMP WITH TIME ZONE`). O painel converte para
  America/Sao_Paulo na exibição.

`preco_por_kg` NÃO é coluna de banco: é calculado no dashboard (preco / peso_g * 1000).

A conexão é responsabilidade de `scrapers/base.py` — este módulo não conhece a
connection string (ver AGENTS.md §2).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from . import base


class Base(DeclarativeBase):
    """Base declarativa do projeto."""


class Produto(Base):
    __tablename__ = "produtos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String, nullable=False)
    marca: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    peso_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    torra: Mapped[str | None] = mapped_column(String, nullable=True)
    processo: Mapped[str | None] = mapped_column(String, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ultima_visao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    precos: Mapped[list["Preco"]] = relationship(
        back_populates="produto", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Produto {self.id} {self.nome!r} peso_g={self.peso_g}>"


class Preco(Base):
    """Snapshot de preço. Uma linha por produto por coleta (sem dedupe de valor)."""

    __tablename__ = "precos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    produto_id: Mapped[int] = mapped_column(
        ForeignKey("produtos.id"), nullable=False, index=True
    )
    preco: Mapped[float] = mapped_column(Float, nullable=False)
    preco_original: Mapped[float | None] = mapped_column(Float, nullable=True)
    em_promocao: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_coleta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    produto: Mapped[Produto] = relationship(back_populates="precos")

    __table_args__ = (Index("idx_precos_produto_data", "produto_id", "data_coleta"),)


class Coleta(Base):
    """Resumo de cada execução do scraper (auditoria)."""

    __tablename__ = "coletas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalizada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paginas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requisicoes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    itens_vistos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    itens_gravados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="em_andamento")
    mensagem: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Coleta {self.id} {self.status} itens={self.itens_gravados}>"


def criar_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Cria o engine do banco (delega a `base`, fonte única da connection string)."""
    return base.criar_engine(url, echo=echo)


def criar_schema(engine: Engine) -> None:
    """Aplica o schema (idempotente)."""
    Base.metadata.create_all(engine)
