"""
check_health_contests.py
========================
Weekly monitor for public health job openings (concursos públicos de saúde).

Currently monitors: SPMS (via WebRHV JSON API)
Search terms: nutrição, informática, informação

Runs every Sunday. Always sends a Telegram notification:
  - POSITIVE: found 1+ matching offers
  - NEGATIVE: nothing found this week

Usage:
  python check_health_contests.py              # Normal run (sends Telegram)
  python check_health_contests.py --dry-run    # Print only, no Telegram
  python check_health_contests.py --schedule-check  # Exit if not 14h Lisbon time
"""

import os
import sys
import argparse
import unicodedata
from datetime import datetime, timezone, timedelta

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# Terms to search for (normalized, accent-insensitive)
SEARCH_TERMS = ["nutri", "informatica", "informacao"]

# SPMS institution code on WebRHV platform
WEBRHV_SPMS_URL = "https://webrhv.min-saude.pt/recrutamento/api/v1/anuncios/instituicoes/MTMzMA"
SPMS_PAGE_URL   = "https://www.spms.min-saude.pt/recrutamento/"

TIMEOUT = 20

# Lisbon timezone (UTC+0 in winter, UTC+1 in summer — handled by pytz-free logic)
LISBON_UTC_OFFSET_WINTER = 0
LISBON_UTC_OFFSET_SUMMER = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Remove accents and lowercase for accent-insensitive matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def is_relevant(item: dict) -> bool:
    """Return True if the item contains any search term."""
    # Fields to check in the WebRHV JSON
    fields_to_check = [
        item.get("titulo", ""),
        item.get("descricao", ""),
        item.get("categoria", ""),
        item.get("carreira", ""),
        item.get("grupo", ""),
    ]
    combined = normalize(" ".join(str(f) for f in fields_to_check if f))
    return any(term in combined for term in SEARCH_TERMS)


def lisbon_hour() -> int:
    """Return the current hour in Lisbon time (approximation via DST rules)."""
    now_utc = datetime.now(timezone.utc)
    # Portugal observes DST: last Sunday of March → last Sunday of October
    year = now_utc.year
    # Last Sunday of March
    march_31 = datetime(year, 3, 31, 1, 0, 0, tzinfo=timezone.utc)
    dst_start = march_31 - timedelta(days=march_31.weekday() + 1 if march_31.weekday() < 6 else 0)
    if march_31.weekday() == 6:
        dst_start = march_31
    # Last Sunday of October
    oct_31 = datetime(year, 10, 31, 1, 0, 0, tzinfo=timezone.utc)
    dst_end = oct_31 - timedelta(days=oct_31.weekday() + 1 if oct_31.weekday() < 6 else 0)
    if oct_31.weekday() == 6:
        dst_end = oct_31

    offset = LISBON_UTC_OFFSET_SUMMER if dst_start <= now_utc < dst_end else LISBON_UTC_OFFSET_WINTER
    return (now_utc.hour + offset) % 24


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_spms_offers() -> tuple[list[dict], str | None]:
    """
    Fetch job offers from SPMS via the WebRHV JSON API.
    Returns (list_of_matching_items, error_message_or_None).
    """
    try:
        resp = requests.get(
            WEBRHV_SPMS_URL,
            timeout=TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; HealthContestBot/2.0)",
            }
        )
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list):
            return [], "Resposta inesperada da API (não é uma lista)"

        matches = [item for item in data if is_relevant(item)]
        return matches, None

    except requests.exceptions.RequestException as e:
        return [], str(e)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> None:
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARNING] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        print("[Telegram] Mensagem enviada com sucesso.")
    except requests.exceptions.RequestException as e:
        print(f"[Telegram] Erro ao enviar: {e}")


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def build_message(matches: list[dict], error: str | None, now: datetime) -> str:
    date_str = now.strftime("%d/%m/%Y às %H:%M")

    lines = [f"🏥 <b>MONITOR DE CONCURSOS SPMS</b>"]
    lines.append(f"📅 Verificação semanal: {date_str}\n")

    if error:
        lines.append(f"⚠️ <b>Erro ao aceder à API SPMS:</b>")
        lines.append(f"<code>{error}</code>\n")

    if matches:
        lines.append(f"✅ <b>Encontrado(s) {len(matches)} concurso(s) relevante(s)!</b>")
        lines.append(f"🎯 Termos: Nutrição | Informática | Informação\n")
        for item in matches:
            titulo = item.get("titulo") or item.get("designacao") or "(sem título)"
            lines.append(f"• <b>{titulo}</b>")
        lines.append(f"\n🔗 <a href=\"{SPMS_PAGE_URL}\">Ver em SPMS</a>")
    else:
        if not error:
            lines.append("✅ <b>Sem concursos relevantes esta semana.</b>")
            lines.append(f"🎯 Termos pesquisados: Nutrição | Informática | Informação")
            lines.append(f"\n🔗 <a href=\"{SPMS_PAGE_URL}\">SPMS Recrutamento</a>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SPMS Health Contests Monitor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print message but do not send Telegram notification")
    parser.add_argument("--schedule-check", action="store_true",
                        help="Exit without running if current Lisbon hour != 14")
    args = parser.parse_args()

    # Schedule gate: only proceed at 14h Lisbon time
    if args.schedule_check:
        hour = lisbon_hour()
        print(f"[Schedule] Hora atual em Lisboa: {hour}h")
        if hour != 14:
            print(f"[Schedule] Não é 14h (é {hour}h) — a sair sem executar.")
            sys.exit(0)

    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')} UTC] A verificar concursos SPMS...")

    matches, error = fetch_spms_offers()

    if error:
        print(f"[SPMS] Erro: {error}")
    else:
        print(f"[SPMS] {len(matches)} concurso(s) relevante(s) encontrado(s).")

    message = build_message(matches, error, now)
    print("\n--- MENSAGEM ---")
    print(message)
    print("--- FIM ---\n")

    if args.dry_run:
        print("[Dry-run] Telegram NÃO enviado.")
    else:
        send_telegram(message)


if __name__ == "__main__":
    main()
