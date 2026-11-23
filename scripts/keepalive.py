"""Keep-alive do dashboard na Streamlit Community Cloud.

A Community Cloud hiberna apps sem tráfego por 12h
(https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app).
Este script visita o app como um visitante real (navegador headless): se
acordado, dwell de 10s para registrar tráfego; se dormindo, clica
"Yes, get this app back up!" e espera o boot.

O health do container é a única fonte de verdade: `/~/+/_stcore/health`
responde `ok` com o app no ar, 400 enquanto dorme e 503 enquanto o container
sobe. Não existe caminho rápido para `ok` — o cold start leva dezenas de
segundos, então a confirmação é polling até BOOT_DEADLINE, nunca uma checagem
única logo após o clique.

Saida: 0 = vivo (ou acordado com sucesso), 1 = falha.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

APP_URL = "https://coffee-monitor-live.streamlit.app"
HEALTH_URL = f"{APP_URL}/~/+/_stcore/health"
WAKE_BUTTON = '[data-testid="wakeup-button-viewer"]'
APP_FRAME = 'iframe[src*="/~/+/"]'  # o app vive num iframe, não na raiz
APP_MARKER = '[data-testid="stApp"]'

BOOT_DEADLINE = 300  # s — cold start do container + boot do Streamlit
POLL_INTERVALO = 5  # s
DWELL = 10  # s de sessão real, para contar como tráfego


def log(msg: str) -> None:
    """Log com timestamp e flush: stdout bufferizado mente sobre o tempo."""
    print(f"{datetime.now(timezone.utc):%H:%M:%S}Z {msg}", flush=True)


def saudavel(page) -> bool:
    try:
        resp = page.request.get(HEALTH_URL, timeout=30_000)
    except Exception as exc:  # rede instável durante o boot não é falha final
        log(f"health: erro {type(exc).__name__}")
        return False
    ok = resp.ok and resp.text().strip() == "ok"
    log(f"health: {resp.status} ok={ok}")
    return ok


def esperar_saudavel(page, limite: float) -> bool:
    """Polling do health até o app responder `ok` ou estourar o limite."""
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if saudavel(page):
            return True
        time.sleep(POLL_INTERVALO)
    return False


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # 1) o health do container diz a verdade (a raiz não)
        acordado = saudavel(page)

        # 2) visita real: renova a janela de 12h quando vivo,
        #    mostra a tela de sono quando não
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=90_000)
        if not acordado:
            btn = page.locator(WAKE_BUTTON)
            btn.first.wait_for(timeout=30_000)
            btn.first.click()
            log(f"botão de wake clicado — aguardando boot (até {BOOT_DEADLINE}s)")
            if not esperar_saudavel(page, BOOT_DEADLINE):
                log("FALHOU: container não ficou saudável dentro do limite")
                browser.close()
                return 1
            # o frontend só renderiza o app depois do container no ar
            page.goto(APP_URL, wait_until="domcontentloaded", timeout=90_000)

        # marcador preciso: o stApp dentro do iframe do app — o iframe
        # sozinho existe até na tela de sono, e a raiz também hospeda o
        # statuspage da Community Cloud
        page.frame_locator(APP_FRAME).locator(APP_MARKER).wait_for(timeout=180_000)
        time.sleep(DWELL)  # dwell: sessão registrada como tráfego
        ok = saudavel(page)
        log(f"resultado: {'VIVO' if ok else 'FALHOU'}")
        browser.close()
        return 0 if ok else 1


sys.exit(main())