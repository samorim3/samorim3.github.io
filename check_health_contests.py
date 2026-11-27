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

Robust networking:
  - Custom SSL context with DEFAULT@SECLEVEL=1 & OP_LEGACY_SERVER_CONNECT (fixes ConnectionResetError 104 on OpenSSL 3)
  - Full browser headers passed to both requests and curl
  - Multi-tier fallback: requests (HTTP/1.1) -> curl (HTTP/1.1 with SECLEVEL=1) -> curl (HTTP/2)
  - Validation ensuring 403 error pages are not treated as valid content

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
import json
import ssl
import subprocess
import argparse
import unicodedata
from datetime import datetime, date, timezone, timedelta
from typing import Tuple, List, Dict, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration & Headers
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

SEARCH_TERMS = ["nutri", "informatica", "informacao"]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

TIMEOUT = 25

PT_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12
}


# ---------------------------------------------------------------------------
# Robust Network Client (Legacy SSL + curl fallback)
# ---------------------------------------------------------------------------

class LegacySSLAdapter(HTTPAdapter):
    """Permits legacy SSL ciphers required by older Portuguese health servers on OpenSSL 3."""
    def init_poolmanager(self, *args, **kwargs):
        try:
            from urllib3.util.ssl_ import create_urllib3_context
            ctx = create_urllib3_context()
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            # Enable legacy renegotiation on OpenSSL 3 (0x4 = OP_LEGACY_SERVER_CONNECT)
            ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
            kwargs["ssl_context"] = ctx
        except Exception:
            pass
        return super().init_poolmanager(*args, **kwargs)

SESSION = requests.Session()
adapter = LegacySSLAdapter()
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)
SESSION.headers.update(HTTP_HEADERS)


def robust_fetch(url: str, is_json: bool = False, timeout: int = TIMEOUT) -> Any:
    """
    Fetch URL with requests Session (Legacy SSL + headers).
    If that fails (403 WAF block or ConnectionResetError 104), falls back to curl.
    """
    last_err = None

    # Attempt 1: requests.Session with LegacySSLAdapter
    try:
        r = SESSION.get(url, verify=False, timeout=timeout)
        if r.status_code == 200:
            if is_json:
                return r.json()
            if len(r.text) > 300 and "403 Forbidden" not in r.text and "Access Denied" not in r.text:
                return r.text
        last_err = f"HTTP {r.status_code}"
    except Exception as e:
        last_err = str(e)

    # Attempt 2: curl fallback with SECLEVEL=1 and browser headers
    curl_bin = "curl.exe" if sys.platform == "win32" else "curl"
    header_args = []
    for k, v in HTTP_HEADERS.items():
        header_args.extend(["-H", f"{k}: {v}"])

    # Try HTTP/1.1 first (webrhv doesn't support HTTP/2), then HTTP/2 (bypasses WAF on cloud IPs)
    for extra_args in [[], ["--http2"]]:
        try:
            cmd = [
                curl_bin, "-sS", "-L", "-k",
                "--ciphers", "DEFAULT@SECLEVEL=1",
            ] + header_args + extra_args + [url]

            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if res.returncode == 0:
                stdout = res.stdout.strip()
                if is_json and (stdout.startswith("[") or stdout.startswith("{")):
                    return json.loads(stdout)
                elif not is_json and len(stdout) > 300 and "403 Forbidden" not in stdout and "Access Denied" not in stdout:
                    return stdout
        except Exception as e:
            last_err = f"{last_err}; curl: {e}"

    raise Exception(last_err or f"Falha ao aceder a {url}")


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
        data = robust_fetch(url, is_json=True)
        if not isinstance(data, list):
            return [], "Resposta inválida da API WebRHV"

        for item in data:
            title = item.get("titulo") or item.get("designacao") or ""
            desc = item.get("descricao") or ""
            comb = f"{title} {desc}"
            if not is_relevant(comb):
                continue

            dt_fim_raw = item.get("dataFim") or item.get("dataFimCandidatura") or item.get("dataTermino")
            dt_fim = parse_pt_date(dt_fim_raw) if dt_fim_raw else None
            if dt_fim and dt_fim < today:
                continue

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
        html = robust_fetch(url, is_json=False)
        soup = BeautifulSoup(html, "html.parser")

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

            m_fim = re.search(r'Data de fim de submissão de candidaturas\s*(\d{4}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{1,2})', full_text)
            deadline = parse_pt_date(m_fim.group(1)) if m_fim else None

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
            html = robust_fetch(page_url, is_json=False)
            soup = BeautifulSoup(html, "html.parser")

            cards = soup.find_all("div", class_="card")
            for card in cards:
                hdr = card.find("div", class_="card-header")
                bdy = card.find("div", class_="card-body")
                title = hdr.get_text(separator=" ", strip=True) if hdr else ""
                bdy_text = bdy.get_text(separator=" ", strip=True) if bdy else ""

                if not (is_relevant(title) or is_relevant(bdy_text)):
                    continue

                has_final_list = bool(re.search(
                    r'ordena[çc][ãa]o\s+final|classifica[çc][ãa]o\s+final|lista\s+final|homologa[çc][ãa]o',
                    bdy_text, re.I
                ))
                if has_final_list:
                    continue

                m_term = re.search(r'T[eé]rmino\s+do\s+prazo\s+de\s+candidatura:\s*([^\n<|]+)', bdy_text, re.I)
                deadline = parse_pt_date(m_term.group(1)) if m_term else None

                if deadline and deadline < today:
                    continue

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
        html = robust_fetch(page_url, is_json=False)
        soup = BeautifulSoup(html, "html.parser")
        
        # Look for postZone or fall back to any content container or body
        pz = (
            soup.find("div", class_=re.compile(r"postZone", re.I))
            or soup.find("div", class_=re.compile(r"entry-content|content-wrapper|mainContents", re.I))
            or soup.body
        )
        if not pz:
            return [], "Conteúdo da página não encontrado"

        elements = pz.find_all(["p", "h2", "h3", "h4"])
        current_title = ""
        current_link = page_url
        current_block = []

        def process_block(title: str, block: List[str], link: str):
            block_txt = " ".join(block)
            if not is_relevant(title + " " + block_txt):
                return

            is_closed = bool(re.search(
                r'ata\s+[2-9]|lista\s+(?:de\s+candidatos|provis[oó]ria|definitiva|unit[aá]ria|final)|grelha\s+de\s+avalia[çc][ãa]o|ordena[çc][ãa]o\s+final|homologa',
                block_txt, re.I
            ))
            if is_closed:
                return

            if re.search(r'/(201\d|202[0-5])/', block_txt):
                return

            dates_in_block = [parse_pt_date(m.group(0)) for m in re.finditer(r'(\d{1,2})\s+de\s+([a-zA-Zç]+)\s+de\s+(\d{4})', block_txt, re.I)]
            dates_in_block = [d for d in dates_in_block if d]
            max_date = max(dates_in_block) if dates_in_block else None

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
        html = robust_fetch(url, is_json=False)
        soup = BeautifulSoup(html, "html.parser")

        articles = soup.find_all("article")
        for art in articles:
            h = art.find(["h1", "h2", "h3"])
            title = h.get_text(strip=True) if h else ""
            if not is_relevant(title):
                continue

            a_tag = art.find("a", href=True)
            link = a_tag["href"] if a_tag else url

            dt_el = art.find(class_=re.compile(r'date|time|entry-meta', re.I))
            dt_text = dt_el.get_text(strip=True) if dt_el else ""
            dt_open = parse_pt_date(dt_text)

            if dt_open and (today - dt_open).days > 25:
                continue

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
