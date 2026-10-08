"""Captura as telas do README a partir da aplicação em execução (Playwright, Chromium).

Executado por scripts/capture-screenshots.sh dentro do container oficial do Playwright, com
--network host. Faz login com os usuários de teste (credenciais recebidas por variável de
ambiente, lidas de .test-users e .env pelo wrapper), cria conversas reais e salva as imagens em
assets/screenshots/. Nenhuma chamada ao Google além de uma pergunta pública ao gemini.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import base64

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

OUT = os.environ.get("OUT_DIR", "/work/assets/screenshots")
CHAT = os.environ.get("CHAT_URL", "http://localhost:3080")
LANGFUSE = os.environ.get("LANGFUSE_URL", "http://localhost:3000")
AUDIT = os.environ.get("AUDIT_URL", "http://localhost:3090")
PROJECT = os.environ.get("LANGFUSE_PROJECT", "corporate-ai-platform")
CRED = json.loads(os.environ["CREDENTIALS"])  # {"email": "senha"}
ONLY = set(filter(None, os.environ.get("ONLY", "").split(",")))
VIEWPORT = {"width": 1440, "height": 900}


def shot(page, name, full=False, height=None):
    path = os.path.join(OUT, f"{name}.png")
    clip = {"x": 0, "y": 0, "width": VIEWPORT["width"], "height": height} if height else None
    page.screenshot(path=path, full_page=full, clip=clip)
    print(f"  ✔ {name}.png", flush=True)


def wanted(section):
    return not ONLY or section in ONLY


# --- LibreChat ------------------------------------------------------------------------------

def chat_login(page, email):
    page.goto(f"{CHAT}/login")
    page.get_by_label("E-mail").or_(page.locator("input[type=email]")).first.fill(email)
    page.locator("input[type=password]").fill(CRED[email])
    page.locator("button[type=submit]").first.click()
    page.wait_for_url("**/c/**", timeout=30000)
    page.wait_for_timeout(2500)


def new_chat(page):
    page.goto(f"{CHAT}/c/new")
    page.wait_for_timeout(2500)


def open_models(page):
    """Abre o seletor de modelos (botão do topo) e o submenu do endpoint Corporate AI."""
    page.locator("button").filter(has_text=re.compile(r"^\s*(local-ai|auto|gemini)\s*$")).first.click()
    page.wait_for_timeout(800)
    page.get_by_text("Corporate AI", exact=True).last.click()
    page.wait_for_timeout(800)


def pick_model(page, model):
    open_models(page)
    page.get_by_role("option", name=model, exact=True).or_(
        page.get_by_role("menuitem", name=model, exact=True)).or_(
        page.get_by_text(model, exact=True)).last.click()
    page.wait_for_timeout(800)


def send(page, text, wait_ms=15000):
    box = page.get_by_role("textbox").last
    box.click()
    box.fill(text)
    page.keyboard.press("Enter")
    page.wait_for_timeout(wait_ms)


def librechat(browser):
    ctx = browser.new_context(viewport=VIEWPORT, locale="pt-BR", color_scheme="light")
    page = ctx.new_page()
    page.goto(f"{CHAT}/login")
    page.wait_for_timeout(2000)
    shot(page, "01-chat-login")

    chat_login(page, "ana.teste@corporate-ai.local")
    new_chat(page)
    open_models(page)
    shot(page, "02-chat-seletor-modelos")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")

    new_chat(page)
    pick_model(page, "auto")
    send(page, "Quanto é 15% de 200?", 12000)
    shot(page, "03-chat-auto-pergunta-simples")

    new_chat(page)
    pick_model(page, "gemini")
    send(page, "Qual a diferença entre REST e GraphQL? Responda em três frases curtas.", 15000)
    shot(page, "04-chat-gemini-pergunta-publica")

    new_chat(page)
    pick_model(page, "gemini")
    send(page, "Analise o contrato confidencial do cliente XPTO e resuma os riscos em duas frases.", 25000)
    shot(page, "05-chat-confidencial-ia-local")

    new_chat(page)
    pick_model(page, "gemini")
    send(page, "A senha do servidor de produção é Prod@2026! Guarde para mim.", 8000)
    shot(page, "06-chat-segredo-bloqueado")
    ctx.close()

    ctx = browser.new_context(viewport=VIEWPORT, locale="pt-BR", color_scheme="light")
    page = ctx.new_page()
    chat_login(page, "bruno.teste@corporate-ai.local")
    new_chat(page)
    open_models(page)
    shot(page, "07-chat-seletor-finance")
    ctx.close()


# --- Langfuse -------------------------------------------------------------------------------

def langfuse_api(filters):
    auth = base64.b64encode(f"{os.environ['LANGFUSE_PK']}:{os.environ['LANGFUSE_SK']}".encode()).decode()
    params = urllib.parse.urlencode({"filter": json.dumps(filters), "limit": "5",
                                     "fields": "core,basic,metadata,trace_context"})
    req = urllib.request.Request(f"{LANGFUSE}/api/public/v2/observations?{params}",
                                 headers={"Authorization": f"Basic {auth}"})
    return json.load(urllib.request.urlopen(req, timeout=20)).get("data", [])


def langfuse(browser):
    ctx = browser.new_context(viewport=VIEWPORT, locale="pt-BR", color_scheme="light")
    page = ctx.new_page()
    page.goto(f"{LANGFUSE}/auth/sign-in")
    page.get_by_placeholder("jsdoe@example.com").fill(os.environ["LANGFUSE_EMAIL"])
    page.locator("input[type=password]").fill(os.environ["LANGFUSE_PASSWORD"])
    page.locator("button[type=submit]").first.click()
    page.wait_for_timeout(4000)

    page.goto(f"{LANGFUSE}/project/{PROJECT}/traces")
    page.wait_for_timeout(5000)
    shot(page, "08-langfuse-traces")

    ana = {"type": "string", "column": "userId", "operator": "=", "value": "ana.teste@corporate-ai.local"}
    for classification, name in (("CONFIDENTIAL", "09-langfuse-trace-redirecionado"),
                                 ("RESTRICTED", "09b-langfuse-segredo-redigido")):
        rows = langfuse_api([ana,
            {"type": "stringObject", "column": "metadata", "key": "classification", "operator": "=", "value": classification},
            {"type": "stringObject", "column": "metadata", "key": "requested_model", "operator": "=", "value": "gemini"}])
        if rows:
            page.goto(f"{LANGFUSE}/project/{PROJECT}/traces/{rows[0]['traceId']}")
            page.wait_for_timeout(5000)
            shot(page, name)

    page.goto(f"{LANGFUSE}/project/{PROJECT}/traces")
    page.wait_for_timeout(4000)
    try:
        page.get_by_text("Trace Tags", exact=True).first.click()
        page.wait_for_timeout(1500)
        option = page.get_by_text("routing:SECURITY_POLICY", exact=True).first
        option.hover()
        page.wait_for_timeout(500)
        option.locator("xpath=ancestor::*[.//text()[normalize-space()='Only']][1]").get_by_text("Only").click()
        page.wait_for_timeout(4000)
        shot(page, "10-langfuse-filtro-por-tag")
    except PWTimeout:
        print("  ✘ filtro por tag: elemento não encontrado", flush=True)
    ctx.close()


# --- Portal de auditoria ----------------------------------------------------------------------

def audit_login(page, email):
    page.goto(f"{AUDIT}/login")
    page.locator("input[name=email]").fill(email)
    page.locator("input[name=password]").fill(CRED[email])
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(2500)


def audit(browser):
    ctx = browser.new_context(viewport=VIEWPORT, locale="pt-BR", color_scheme="light")
    page = ctx.new_page()
    page.goto(f"{AUDIT}/login")
    shot(page, "11-auditoria-login")

    audit_login(page, "diego.master@corporate-ai.local")
    page.goto(f"{AUDIT}/?run=1&user=ana.teste%40corporate-ai.local")
    page.wait_for_timeout(3000)
    shot(page, "12-auditoria-busca-master", full=True)

    page.goto(f"{AUDIT}/?run=1&user=ana.teste%40corporate-ai.local&classification=CONFIDENTIAL&requested=gemini")
    page.wait_for_timeout(3000)
    link = page.get_by_role("link", name="abrir conversa").first
    href = link.get_attribute("href")
    page.goto(f"{AUDIT}{href}")
    page.wait_for_timeout(1500)
    page.locator("textarea[name=reason]").fill("Investigação do chamado 42 — contrato do cliente XPTO")
    shot(page, "13-auditoria-motivo")
    page.locator("button[type=submit]").last.click()
    page.wait_for_timeout(3000)
    shot(page, "14-auditoria-conversa", full=True)

    page.goto(f"{AUDIT}/access-log")
    page.wait_for_timeout(2500)
    # Recorte nos eventos mais recentes (desta execução): eventos de CLI trazem usuário@máquina do operador.
    shot(page, "15-auditoria-trilha", height=665)
    page.locator("form[action='/logout'] button").click()
    ctx.close()

    ctx = browser.new_context(viewport=VIEWPORT, locale="pt-BR", color_scheme="light")
    page = ctx.new_page()
    audit_login(page, "carla.auditora@corporate-ai.local")
    page.goto(f"{AUDIT}/?run=1&user=ana.teste%40corporate-ai.local&classification=CONFIDENTIAL")
    page.wait_for_timeout(3000)
    shot(page, "16-auditoria-auditor-so-metadados", full=True)
    page.goto(f"{AUDIT}{href}")
    page.wait_for_timeout(1500)
    shot(page, "17-auditoria-auditor-negado")
    page.locator("form[action='/logout'] button").click()
    ctx.close()

    ctx = browser.new_context(viewport=VIEWPORT, locale="pt-BR", color_scheme="light")
    page = ctx.new_page()
    audit_login(page, "edu.admin@corporate-ai.local")
    shot(page, "18-auditoria-admin-recusado")
    ctx.close()


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, fn in (("chat", librechat), ("langfuse", langfuse), ("audit", audit)):
            if wanted(name):
                print(f"== {name}", flush=True)
                fn(browser)
                if name == "chat":
                    time.sleep(15)  # ingestão no Langfuse
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
