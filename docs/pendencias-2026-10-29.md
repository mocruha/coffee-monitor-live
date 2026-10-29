# Pendências e sugestões — coffee-monitor-live (para retomar depois)

> Criado 2026-10-29. Este arquivo é o "estado do jogo" para uma sessão futura: o que ficou pronto, o que espera decisão sua, e como executar cada uma delas.
> Revisão COMPLETA (com evidência arquivo:linha de cada item): `docs/revisao-2026-10-29.md`.

## Estado atual (não refazer nada disso)

- Repo clonado em `/home/moc/Projects/coffee-monitor-live`, configurado com o App `mocruha-hermes` (`hermes-git-setup` já rodado — commits saem como `mocruha-hermes[bot]`).
- 3 commits LOCAIS em `main`, **nada com push**:
  - `4101bac` docs: plano de out/2026 versionado
  - `c5a6000` chore: keep-alive (`scripts/keepalive.py` + `.github/workflows/keepalive.yml`, validados)
  - `340340e` docs: revisão do dashboard
- Guias/artefatos fora do repo: `~/Projects/vps/guia-bot-keepalive-coffee.md` e `~/Projects/vps/n8n-coffee-keepalive.json` (JSON válido, nó de alerta = placeholder).
- Enquanto não houver push: o dashboard segue sujeito a hibernar em 12 h (o bot existe só no disco).

## As 3 decisões pendentes

### D1 — Triagem: quais itens da revisão virar código

Definições: P1 = errado hoje com dados reais; P2 = frágil/improvável/cosmético.

**Lote recomendado: A-1 + A-2 + B-1 (bugs P1) + C-1 (feature).** Justificativa em 1 linha cada:

| Item | O que é | Por que esse lote |
|---|---|---|
| A-1 (P1) | Produto sem peso some de TODAS as vistas silenciosamente (`app.py:84` descarta NaN no filtro) | Bug real e invisível; corrigir exige junto formatador nulo-seguro (senão o texto "nang" volta a aparecer — ver R-2 do relatório) |
| A-2 (P1) | Homônimos: gráfico sempre mostra o 1º de 2 produtos com mesmo nome (`app.py:163-167`); hoje há 2 pares reais | Histórico de ~2 de 51 produtos inacessível; fix = selectbox por (nome+peso/url) |
| B-1 (P1) | Cache 1 h (`app.py:30,35`) + coleta às 20h = dado velho até 60 min, sem botão de refresh | Fix barato: botão "↻ Atualizar" com `st.cache_data.clear(); st.rerun()` |
| C-1 | Índice da cesta: mediana R$/kg/dia normalizada em 100 | Única métrica que responde "café subiu?"; SQL puro sobre o schema atual, sem migração |

Restante da mesa (decidir quando der vontade): A-3 slider min==max crasha a página; A-4 slider NaN esvazia tudo; A-5 histórico R$/kg reescalado pelo peso de hoje (degrau falso ao mudar embalagem); A-6 rótulo "1kg" para ≥1000 g (latente); A-7 código morto (`variacao_abs`, `visto_em`); B-2 variação sem ↑/↓ (a spec promete; `format="delta"` NÃO existe no 1.65 — usar `format="%+.2f%%"` ou prefixar ↑/↓ no `formatar_pct`); B-3 "Maior queda 0,00%" e falta "Maior alta"; B-4 contador "X de Y" com denominadores inconsistentes; B-5 mensagem de falha da coleta já vem do banco mas nunca é exibida; C-2 profundidade de desconto; C-3 dias sem coleta; C-4 preço vs piso do produto; C-5 frequência de promoção.

**Protocolo de execução (TDD, definido no plano):**
```bash
cd /home/moc/Projects/coffee-monitor-live
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```
Para cada item aprovado: (1) teste falho em `tests/test_dashboard_dados.py` reproduzindo o bug → rodar `.venv/bin/pytest tests/ -k <tag>` e ver FALHAR; (2) correção mínima em `dashboard/`; (3) suíte completa passando (roda offline em SQLite — nada de Neon); (4) um commit por item (`fix: ...`). Branch + PR, nunca push direto em main.

### D2 — Publicar (push) e ativar o keep-alive

O que falta no mundo: os commits locais e o workflow de 6 h. Caminhos:

- **Recomendado (meu fluxo, com sua autorização):** eu movo os 3 commits para um branch (`chore/keepalive-bot-e-docs`), main local volta a bater com origin, push do branch via `gha` (token de 1 h do App, seu keyring não toca nada), abro PR e te passo a URL — merge é seu, na UI.
- Alternativa: você mesmo `git push origin main` com seu gh pessoal (pode; agente não pode).
- Depois do merge, ativação em 2 passos:
  1. GitHub → Actions → workflow **keepalive** → **Run workflow** (disparo manual = smoke test). Esperado no log: `health: 200 acordado=...` e `resultado: VIVO (200)`.
  2. Nada a "ligar": o cron `5 */6 * * *` vale sozinho a partir do branch padrão. Gotcha: GitHub pausa schedules de workflows sem atividade por 60 dias.
- Importar o n8n: `n8n.mogus.fyi` → Workflows → Import from File → `~/Projects/vps/n8n-coffee-keepalive.json` → ativar. Teste: trocar `/health` por `/nope` e rodar manualmente → o alerta deve disparar.

### D3 — Canal de alerta da camada 1 (último nó do JSON n8n, hoje placeholder)

Critério: o alerta não pode depender do mesmo ponto de falha que monitora (VPS roda o n8n → alerta self-host na VPS não serve). Opções:

1. **Telegram** — recomendado: @BotFather gera token, nó Telegram no n8n, ~10 min, zero custo, push no celular.
2. E-mail SMTP com app password (o padrão antigo da máquina velha; risco de cair no spam).
3. ntfy **público** (ntfy.sh) ou self-host fora da VPS.
4. Nenhum: deixar camada 1 inativa sem culpa — é a camada 2 (Actions) que mantém vivo; a 1 só avisa.

## Como retomar

Sente aqui e diga, por exemplo: "retomar pendências do coffee-monitor" — o checklist de decisões está neste arquivo; a evidência linha a linha está no `docs/revisao-2026-10-29.md`. Os dois guias de referência: `~/Projects/vps/guia-bot-keepalive-coffee.md` (bot + ativação + rollback) e os planos originais em `~/Projects/.hermes/plans/`.
