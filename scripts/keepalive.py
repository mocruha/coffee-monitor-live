"""Keep-alive do dashboard na Streamlit Community Cloud.

A Community Cloud hiberna apps sem tráfego por 12h e o wake exige clique
humano. Este script visita o app como um visitante real (navegador
headless): se acordado, dwell 10s para registrar tráfego; se dormindo,
clica "Yes, get this app back up!" e espera o boot.

Saida: 0 = vivo (ou acordado com sucesso), 1 = falha.
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

APP_URL = "https://coffee-monitor-live.streamlit.app"
HEALTH_URL = f"{APP_URL}/~/+/_stcore/health"
WAKE_BUTTON = '[data-testid="wakeup-button-viewer"]'
APP_MARKER = '[data-testid="stApp"]'


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # 1) o health do container diz a verdade (a raiz não)
        resp = page.request.get(HEALTH_URL, timeout=30_000)
        acordado = resp.ok and resp.text().strip() == "ok"
        print(f"health: {resp.status} acordado={acordado}")

        # 2) visita real: renova a janela de 12h quando vivo,
        #    mostra a tela de sono quando não
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=90_000)
        if not acordado:
            btn = page.locator(WAKE_BUTTON)
            btn.first.wait_for(timeout=30_000)
            btn.first.click()
            print("botão de wake clicado — aguardando boot")

        page.wait_for_selector(f"iframe, {APP_MARKER}", timeout=180_000)
        time.sleep(10)  # dwell: sessão registrada como tráfego
        alive = page.request.get(HEALTH_URL, timeout=30_000)
        ok = alive.ok and alive.text().strip() == "ok"
        print(f"resultado: {'VIVO' if ok else 'FALHOU'} ({alive.status})")
        browser.close()
        return 0 if ok else 1


sys.exit(main())
