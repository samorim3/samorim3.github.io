"""
check_health_contests.py
========================
Weekly autonomous monitor for public health job openings in Portugal.

Monitored institutions:
  1. SPMS (WebRHV API)
  2. CHMA - Centro Hospitalar do Médio Ave (WM Smile)
  3. Hospital de Braga (WM Smile)
  4. ULS Póvoa de Varzim / Vila do Conde - ULSPVVC (WM Smile)
  5. ULS Santo António - ULSSA (Técnicos Superiores & Informáticos)
  6. ULS Barcelos / Esposende - ULSBE (Concursos Institucionais)
  7. ULS Matosinhos - ULSM (WordPress Concursos)
  8. ULS Alto Minho - ULSAM (WordPress Concursos)

Search terms (accent-insensitive):
  - nutrição (nutri)
  - informática (informatica)
  - informação (informacao)

Validity check (concursos válidos / candidaturas abertas):
  - Verifies submission deadline (data de fim >= today)
  - Discards contests with advanced stages already published (e.g. Ata 2/3/4, Lista de Admitidos, Ordenação Final)
  - For open-date only, verifies opening within standard application window (last 25 days)

Notification:
  - Always sends Telegram message (positive if matches found, negative if none)
"""

import os
import sys
import re
import argparse
import unicodedata
from datetime import datetime, date, timezone, timedelta
from typing import Tuple, List, Dict, Optional

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

SEARCH_TERMS = ["nutri", "informatica", "informacao"]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}

TIMEOUT = 20

PT_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Normalize text: remove accents and convert to lowercase."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def is_relevant(text: str) -> bool:
    """Check if normalized text contains any of the search terms."""
    norm = normalize(text)
    return any(t in norm for t in SEARCH_TERMS)


def parse_pt_date(text: str) -> Optional[date]:
    """Extract date from various Portuguese date formats."""
    if not text:
        return None

    # 1. DD de mês de AAAA
    m = re.search(r'(\d{1,2})\s+de\s+([a-zA-Zç]+)\s+de\s+(\d{4})', text, re.I)
    if m:
        d, mon_str, y = int(m.group(1)), normalize(m.group(2)), int(m.group(3))
        mon = PT_MONTHS.get(mon_str)
        if mon:
            try:
                return date(y, mon, d)
            except ValueError:
                pass

    # 2. AAAA / MM / DD or AAAA-MM-DD
    m = re.search(r'(\d{4})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})', text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # 3. DD/MM/AAAA or DD-MM-AAAA
    m = re.search(r'(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})', text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    return None


def lisbon_hour() -> int:
    """Return the current hour in Lisbon time (with DST approximation)."""
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    march_31 = datetime(year, 3, 31, 1, 0, 0, tzinfo=timezone.utc)
    dst_start = march_31 - timedelta(days=march_31.weekday() + 1 if march_31.weekday() < 6 else 0)
    oct_31 = datetime(year, 10, 31, 1, 0, 0, tzinfo=timezone.utc)
    dst_end = oct_31 - timedelta(days=oct_31.weekday() + 1 if oct_31.weekday() < 6 else 0)

    offset = 1 if dst_start <= now_utc < dst_end else 0
    return (now_utc.hour + offset) % 24


# ---------------------------------------------------------------------------
# Institution Fetchers
# ---------------------------------------------------------------------------

def fetch_spms(today: date) -> Tuple[List[Dict], Optional[str]]:
    """SPMS via WebRHV JSON API."""
    url = "https://webrhv.min-saude.pt/recrutamento/api/v1/anuncios/instituicoes/MTMzMA"
    page_url = "https://www.spms.min-saude.pt/recrutamento/"
    matches = []
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            return [], "Resposta inválida da API WebRHV"

        for item in data:
            title = item.get("titulo") or item.get("designacao") or ""
            desc = item.get("descricao") or ""
            comb = f"{title} {desc}"
            if not is_relevant(comb):
                continue

            # Check validity
            dt_fim_raw = item.get("dataFim") or item.get("dataFimCandidatura") or item.get("dataTermino")
            dt_fim = parse_pt_date(dt_fim_raw) if dt_fim_raw else None
            if dt_fim and dt_fim < today:
                continue  # expired

            matches.append({
                "institution": "SPMS",
                "title": title.strip() or "Oferta de Recrutamento SPMS",
                "link": page_url,
                "deadline": dt_fim.strftime("%d/%m/%Y") if dt_fim else "A decorrer",
            })
        return matches, None
    except Exception as e:
        return [], str(e)


def fetch_wm_smile(name: str, base_url: str, today: date) -> Tuple[List[Dict], Optional[str]]:
    """Fetch from WM Smile (CHMA, Hospital de Braga, ULSPVVC)."""
    url = f"{base_url}/processos-ativos"
    matches = []
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Items are in li elements with pos_ class
        items = soup.find_all("li", class_=lambda c: c and "pos_" in c)
        for li in items:
            title_a = li.find("a", href=re.compile(r"recruitment_process_id"))
            if not title_a:
                continue

            title = title_a.get_text(strip=True)
            link = f"{base_url}{title_a['href']}" if title_a['href'].startswith("/") else title_a['href']
            full_text = li.get_text(separator=" ", strip=True)

            if not (is_relevant(title) or is_relevant(full_text)):
                continue

            # Check deadline
            m_fim = re.search(r'Data de fim de submissão de candidaturas\s*(\d{4}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{1,2})', full_text)
            deadline = parse_pt_date(m_fim.group(1)) if m_fim else None

            # If deadline is in the past, contest is expired
            if deadline and deadline < today:
                continue

            matches.append({
                "institution": name,
                "title": title,
                "link": link,
                "deadline": deadline.strftime("%d/%m/%Y") if deadline else "Em curso",
            })
        return matches, None
    except Exception as e:
        return [], str(e)


def fetch_ulssa(today: date) -> Tuple[List[Dict], Optional[str]]:
    """ULSSA - Técnicos Superiores e Informáticos."""
    urls = [
        ("Técnicos Superiores", "https://www.ulssa.pt/recrutamento/tecnicos-superiores"),
        ("Informáticos", "https://www.ulssa.pt/recrutamento/informaticos"),
    ]
    matches = []
    errors = []

    for category, page_url in urls:
        try:
            r = requests.get(page_url, headers=HTTP_HEADERS, verify=False, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            cards = soup.find_all("div", class_="card")
            for card in cards:
                hdr = card.find("div", class_="card-header")
                bdy = card.find("div", class_="card-body")
                title = hdr.get_text(separator=" ", strip=True) if hdr else ""
                bdy_text = bdy.get_text(separator=" ", strip=True) if bdy else ""

                if not (is_relevant(title) or is_relevant(bdy_text)):
                    continue

                # Check if final results already published
                has_final_list = bool(re.search(
                    r'ordena[çc][ãa]o\s+final|classifica[çc][ãa]o\s+final|lista\s+final|homologa[çc][ãa]o',
                    bdy_text, re.I
                ))
                if has_final_list:
                    continue

                # Check deadline
                m_term = re.search(r'T[eé]rmino\s+do\s+prazo\s+de\s+candidatura:\s*([^\n<|]+)', bdy_text, re.I)
                deadline = parse_pt_date(m_term.group(1)) if m_term else None

                if deadline and deadline < today:
                    continue  # expired!

                # Find announcement PDF link if available
                pdf_link = page_url
                if bdy:
                    a_pdf = bdy.find("a", href=re.compile(r'\.pdf', re.I))
                    if a_pdf and a_pdf.get("href"):
                        href = a_pdf["href"]
                        if href.startswith("http"):
                            pdf_link = href
                        elif href.startswith("../"):
                            pdf_link = f"https://www.ulssa.pt/{href[3:]}"
                        else:
                            pdf_link = f"https://www.ulssa.pt/{href.lstrip('/')}"

                matches.append({
                    "institution": f"ULSSA ({category})",
                    "title": title[:180],
                    "link": pdf_link,
                    "deadline": deadline.strftime("%d/%m/%Y") if deadline else "Em aberto",
                })
        except Exception as e:
            errors.append(f"{category}: {e}")

    err_msg = "; ".join(errors) if errors else None
    return matches, err_msg


def fetch_ulsbe(today: date) -> Tuple[List[Dict], Optional[str]]:
    """ULS Barcelos / Esposende - Concursos Institucionais."""
    page_url = "https://www.ulsbe.min-saude.pt/2018/01/16/concursos-institucionais/"
    matches = []
    try:
        r = requests.get(page_url, headers=HTTP_HEADERS, verify=False, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        pz = soup.find("div", class_="postZone")
        if not pz:
            return [], "Estrutura postZone não encontrada"

        elements = pz.find_all(["p", "h2", "h3", "h4"])
        current_title = ""
        current_link = page_url
        current_block = []

        def process_block(title: str, block: List[str], link: str):
            block_txt = " ".join(block)
            if not is_relevant(title + " " + block_txt):
                return

            # Check if contest is already in advanced stage (applications closed)
            is_closed = bool(re.search(
                r'ata\s+[2-9]|lista\s+(?:de\s+candidatos|provis[oó]ria|definitiva|unit[aá]ria|final)|grelha\s+de\s+avalia[çc][ãa]o|ordena[çc][ãa]o\s+final|homologa',
                block_txt, re.I
            ))
            if is_closed:
                return

            # Check if aviso belongs to an older year (e.g. /2024/, /2025/)
            if re.search(r'/(201\d|202[0-5])/', block_txt):
                return

            # Find any dates mentioned in block
            dates_in_block = [parse_pt_date(m.group(0)) for m in re.finditer(r'(\d{1,2})\s+de\s+([a-zA-Zç]+)\s+de\s+(\d{4})', block_txt, re.I)]
            dates_in_block = [d for d in dates_in_block if d]
            max_date = max(dates_in_block) if dates_in_block else None

            # If last date was over 25 days ago, the application period has closed
            if max_date and (today - max_date).days > 25:
                return

            matches.append({
                "institution": "ULSBE",
                "title": title[:180],
                "link": link,
                "deadline": max_date.strftime("%d/%m/%Y") if max_date else "Recente",
            })

        for el in elements:
            t = el.get_text(separator=" ", strip=True)
            if re.match(r'^(procedimento|concurso|reserva|bolsa|manifesta)', t, re.I):
                if current_title:
                    process_block(current_title, current_block, current_link)

                current_title = t
                a_tag = el.find("a", href=True)
                current_link = a_tag["href"] if a_tag else page_url
                current_block = [t]
            else:
                current_block.append(t)
                a_tag = el.find("a", href=True)
                if a_tag and current_link == page_url:
                    current_link = a_tag["href"]

        if current_title:
            process_block(current_title, current_block, current_link)

        return matches, None
    except Exception as e:
        return [], str(e)


def fetch_wp_category(name: str, base_url: str, today: date) -> Tuple[List[Dict], Optional[str]]:
    """ULSM and ULSAM category/profissional/concursos/."""
    url = f"{base_url}/category/profissional/concursos/"
    matches = []
    try:
        r = requests.get(url, headers=HTTP_HEADERS, verify=False, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.find_all("article")
        for art in articles:
            h = art.find(["h1", "h2", "h3"])
            title = h.get_text(strip=True) if h else ""
            if not is_relevant(title):
                continue

            # Link
            a_tag = art.find("a", href=True)
            link = a_tag["href"] if a_tag else url

            # Opening date
            dt_el = art.find(class_=re.compile(r'date|time|entry-meta', re.I))
            dt_text = dt_el.get_text(strip=True) if dt_el else ""
            dt_open = parse_pt_date(dt_text)

            # Validity: if opened more than 25 days ago, the 10-15 working day window has closed
            if dt_open and (today - dt_open).days > 25:
                continue

            # If title indicates final list
            if re.search(r'lista\s+final|ordena[çc][ãa]o\s+final|homologa', title, re.I):
                continue

            matches.append({
                "institution": name,
                "title": title,
                "link": link,
                "deadline": f"Aberto a {dt_open.strftime('%d/%m/%Y')}" if dt_open else "Recente",
            })
        return matches, None
    except Exception as e:
        return [], str(e)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> None:
    """Send message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[AVISO] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos.")
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
        print("[Telegram] Mensagem enviada com sucesso!")
    except Exception as e:
        print(f"[Telegram] Erro ao enviar mensagem: {e}")


# ---------------------------------------------------------------------------
# Message Builder
# ---------------------------------------------------------------------------

def build_message(matches: List[Dict], errors: Dict[str, str], now: datetime) -> str:
    date_str = now.strftime("%d/%m/%Y às %H:%M")
    lines = [
        "🏥 <b>ALERTA DE CONCURSOS DE SAÚDE</b>",
        f"📅 Verificação semanal: {date_str}",
        "🎯 Termos: <b>Nutrição | Informática | Informação</b>\n",
    ]

    if matches:
        lines.append(f"🚨 <b>Encontrado(s) {len(matches)} concurso(s) válido(s) e em vigor:</b>\n")
        for m in matches:
            lines.append(f"🏥 <b>[{m['institution']}]</b>")
            lines.append(f"📌 {m['title']}")
            lines.append(f"⏳ Prazo / Data: {m['deadline']}")
            lines.append(f"🔗 <a href=\"{m['link']}\">Aceder ao Concurso / Edital</a>\n")
    else:
        lines.append("✅ <b>Sem concursos válidos em aberto esta semana.</b>")
        lines.append("<i>Foram verificadas as 8 instituições de saúde e não existem vagas ativas com os termos indicados.</i>\n")

    if errors:
        lines.append("⚠️ <b>Avisos de acesso:</b>")
        for inst, err in errors.items():
            lines.append(f"• {inst}: <code>{err[:100]}</code>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Controller
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Health Contests Monitor")
    parser.add_argument("--dry-run", action="store_true", help="Print only, do not send Telegram")
    parser.add_argument("--schedule-check", action="store_true", help="Only run at 14h Lisbon time")
    args = parser.parse_args()

    if args.schedule_check:
        hour = lisbon_hour()
        print(f"[Schedule] Hora atual em Lisboa: {hour}h")
        if hour != 14:
            print(f"[Schedule] Não é 14h (é {hour}h) — a sair sem executar.")
            sys.exit(0)

    now = datetime.now(timezone.utc)
    today = date.today()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')} UTC] A iniciar verificação semanal de concursos (data base: {today})...")

    institutions = [
        ("SPMS", lambda: fetch_spms(today)),
        ("CHMA", lambda: fetch_wm_smile("CHMA", "https://recrutamento.chma.min-saude.pt", today)),
        ("Hospital de Braga", lambda: fetch_wm_smile("Hospital de Braga", "https://recrutamento.hospitaldebraga.pt", today)),
        ("ULSPVVC", lambda: fetch_wm_smile("ULSPVVC", "https://recrutamento.ulspvvc.min-saude.pt", today)),
        ("ULSSA", lambda: fetch_ulssa(today)),
        ("ULSBE", lambda: fetch_ulsbe(today)),
        ("ULSM", lambda: fetch_wp_category("ULSM", "https://www.ulsm.min-saude.pt", today)),
        ("ULSAM", lambda: fetch_wp_category("ULSAM", "https://www.ulsam.min-saude.pt", today)),
    ]

    all_matches: List[Dict] = []
    errors: Dict[str, str] = {}

    for name, fetcher in institutions:
        print(f"-> A verificar {name}...")
        try:
            m, err = fetcher()
            if err:
                print(f"   [AVISO] {name}: {err}")
                errors[name] = err
            else:
                print(f"   [OK] {name}: {len(m)} vaga(s) válida(s) encontrada(s)")
            all_matches.extend(m)
        except Exception as e:
            print(f"   [ERRO] {name}: {e}")
            errors[name] = str(e)

    message = build_message(all_matches, errors, now)

    print("\n" + "=" * 50)
    print(message)
    print("=" * 50 + "\n")

    if args.dry_run:
        print("[Dry-run] Telegram NÃO enviado.")
    else:
        send_telegram(message)


if __name__ == "__main__":
    main()
