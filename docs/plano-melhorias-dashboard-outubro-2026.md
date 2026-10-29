# Plano de melhorias do dashboard — coffee-monitor-live

> Contexto: PR #2 mesclado. O gráfico de evolução de preço apresenta bug: com granularidade "Dia", o eixo X mostra **hora/segundo** em vez de data. O usuário pediu também reduzir a coleta para 1x/dia às 20h. Este plano aborda o bug + o cron + a revisão do design.

## Decisões tomadas (4 perguntas, usuário)

1. **Histórico das 08h** (já gravado no banco): manter no banco, **esconder só no gráfico**.
2. **Granularidade do gráfico**: manter os três níveis — **Dia / Semana / Mês**.
3. **Escopo da revisão do reviewer**: **correção + visualização** (não UX nem performance).
4. **Escopo do filtro "só 20h"**: aplicar **apenas na função `grafico()`**; a tabela, barra de status e destaques continuam usando a coleta mais recente.

## Mudanças planejadas (em ordem de execução)

### PR A — Scraper 1x/dia às 20h (`chore/scraper-uma-vez-por-dia`)

**Arquivos:**
- `.github/workflows/scraper.yml`: cron de `"0 11,23 * * *"` → `"0 23 * * *"` (23h UTC = 20h Brasília).
- `AGENTS.md`: atualizar menções ao cron:
  - §1 (missão): "duas vezes por dia" → "uma vez por dia, às 20h"
  - §3 (arquitetura): "Cron: 0 11,23 * * *" → "Cron: 0 23 * * *"
  - §5 (volume): atualizar de "9 req/exec ~18s" para "~5 req/exec ~10s"
  - §7.3 (agendamento): cron e justificativa
  - §9 (considerações) e §11.2 (números de referência): atualizar números

**Nenhum código do scraper muda.**

### PR B — Gráfico: filtro 20h + correção do eixo (`fix/grafico-so-20h-e-eixo-limpo`)

**Arquivo:** `dashboard/app.py` (função `grafico()`)

**O que fazer:**

1. **Filtrar só as coletas das 20h** antes de agregar:
   ```python
   historico = historico[historico["data_local"].dt.hour == 20]
   if len(historico) < 2:
       st.info("Menos de duas coletas às 20h — a evolução aparece a partir da segunda.")
       return
   ```

2. **Corrigir o eixo X**. O `to_period('D').dt.start_time` retorna um `Timestamp` com `00:00:00`, mas Plotly renderiza como datetime com hora quando a série é naive ou quando há inconsistência. Converter para `dt.date` garante eixo com rótulo "YYYY-MM-DD":
   ```python
   historico["periodo"] = historico["data_local"].dt.tz_localize(None).dt.to_period(regra).dt.start_time
   # Para granularidade Dia: converter para dt.date no plot
   if granularidade == "Dia":
       agregado["periodo"] = agregado["periodo"].dt.date
   ```
   (Semana e Mês ficam como estão — o eixo já fica limpo porque `start_time` de 'W' e 'M' sempre é meia-noite do primeiro dia da semana/mês.)

3. **Ajustar caption e labels**:
   - Caption: "Cada ponto é o preço médio do período escolhido. Coletas das 08h são ignoradas."
   - Label do eixo X: `"Data (Brasília)"` em vez de `"Período (horário de Brasília)"` (agora é só data).

**Arquivo:** `AGENTS.md` §7.4 — mencionar que o gráfico filtra só as coletas das 20h.

### PR C — Resultados da revisão do reviewer (após PR B)

Depende do relatório do reviewer. Prováveis achados (hipóteses minhas, a confirmar):

- **`carregar_historico()` sem filtro de horário:** se o reviewer julgar que a filtragem no dashboard é frágil, sugerir uma função `carregar_historico_noturno()` em `dados.py`.
- **`variacao_pct` com LAG:** com 1 coleta/dia, o LAG compara dia a dia. Se algum dia o scraper falhar, o LAG "pula" um dia — a variação passa a ser `dia-N vs dia-(N-2)`, que ainda é informativa. Verificar se o comportamento é aceitável.
- **Agrupamento em Semana:** a semana ISO começa na segunda-feira; pode ser mais intuitivo usar semana civil (domingo). Verificar preferência.

**Se os achados forem pequenos e relacionados:** 1 PR.
**Se forem independentes:** quebrar em PRs temáticos.

## Ordem de execução

```
PR A (cron)  ──→  PR B (gráfico)  ──→  revisão do reviewer  ──→  PR C (resultados)
    │                  │                       │
    ↓                  ↓                       ↓
  rápido            resolve o bug          sugere melhorias
  sem risco         reportado              que viram PR(s)
```

- **PR A e PR B são independentes.** Posso criá-los em paralelo, em branches separadas.
- **Revisão do reviewer** só depois do PR B mesclado (para que ele revise o código já corrigido).
- **PR C** depende dos achados.

## Testes (para PR B)

Exercitar `grafico()` offline com dados sintéticos de **1 ponto por dia às 20h**:
- Confirmar que o eixo X não mostra hora/segundo
- Confirmar que as 3 granularidades agregam corretamente
- Confirmar que coletas das 08h **são ignoradas** no gráfico

## Arquivos envolvidos (resumo)

| Arquivo | PR A | PR B | PR C |
|---|---|---|---|
| `.github/workflows/scraper.yml` | ✅ | — | — |
| `AGENTS.md` | ✅ | ✅ | talvez |
| `dashboard/app.py` | — | ✅ | talvez |
| `dashboard/dados.py` | — | — | talvez |
| `tests/` | — | — | talvez |

## Observações

- **Custo do reviewer:** o reviewer é um subagent pago por passo. Escopo focado (correção + visualização) mantém o custo baixo.
- **Dados das 08h:** continuam no banco (`precos`), acessíveis via `carregar_historico()`. Só o gráfico filtra; tabelas e variações usam o estado mais recente.
- **Nada muda no scraper em si.** A mudança é só de cron (PR A) e dashboard (PR B).
