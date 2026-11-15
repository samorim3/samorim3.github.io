"""
check_health_contests.py
========================
Agente automatizado de monitorização de concursos públicos de saúde (ULS e SPMS).
Verifica semanalmente (ao domingo às 13h15) os links oficiais à procura de concursos
em vigor contendo os termos: "nutrição", "informática", "informação".

Caso haja concursos relevantes ou ocorra algum erro no acesso aos links,
envia alerta automático para o Telegram.
"""

import os
import sys
import io
import json
import re
import socket
import ssl
import subprocess
import unicodedata
import argparse
import html
from datetime import datetime
from urllib.parse import urljoin
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
import urllib3

# Ensure UTF-8 output encoding for terminals
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Suppress insecure request warnings
urllib3.disable_warnings()

# Default socket timeout
socket.setdefaulttimeout(25)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'pt-PT,pt;q=0.9,en;q=0.8',
    'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1'
}

# Configuração de sessão com suporte a cifras legadas (compatibilidade OpenSSL 3 no Linux / Ubuntu)
class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        try:
            from urllib3.util.ssl_ import create_urllib3_context
            ctx = create_urllib3_context()
            ctx.set_ciphers('DEFAULT@SECLEVEL=1')
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            kwargs['ssl_context'] = ctx
        except Exception:
            pass
        return super().init_poolmanager(*args, **kwargs)

SESSION = requests.Session()
adapter = LegacySSLAdapter()
SESSION.mount('https://', adapter)
SESSION.mount('http://', adapter)
SESSION.headers.update(HTTP_HEADERS)

FALSE_POSITIVE_PHRASES = [
    "informacao legal",
    "informacao clinica",
    "acesso a informacao",
    "direito a informacao",
    "pedido de informacao",
    "politica de privacidade",
    "termos e condicoes",
    "politica de cookies",
    "livro de reclamacoes"
]

def normalize(text):
    if not text:
        return ""
    nfkd = unicodedata.normalize('NFKD', str(text))
    return ''.join([c for c in nfkd if not unicodedata.combining(c)]).lower()

def check_keyword_match(text):
    norm = normalize(text)
    
    for fp in FALSE_POSITIVE_PHRASES:
        if fp in norm and len(norm) < 65:
            return False, None
            
    if "nutri" in norm:
        return True, "Nutrição"
    if "informatic" in norm:
        return True, "Informática"
    if any(k in norm for k in ["sistemas de informacao", "tecnologias de informacao", "gestao de informacao", "informacao"]):
        if any(w in norm for w in ["tecnico", "especialista", "assistente", "diretor", "reserva", "concurso", "recrutamento", "procedimento", "bolsa", "estagio", "carreira", "funcoes", "vagas", "area"]):
            return True, "Informação"
        elif "informacao" in norm and not any(fp in norm for fp in FALSE_POSITIVE_PHRASES):
            return True, "Informação"

    return False, None

def robust_fetch(url, is_json=False):
    """
    Tenta efetuar pedido com requests.Session (com cifras compatíveis e headers completos).
    Em caso de bloqueio (403/reset de conexão), faz fallback automático para o curl do sistema com HTTP/2.
    """
    last_error = None
    
    # Tentativa 1: requests com Session configurada
    try:
        r = SESSION.get(url, verify=False, timeout=18)
        if r.status_code == 200 and len(r.text) > 100:
            return r.json() if is_json else r.text
        if r.status_code != 200:
            last_error = f"Status {r.status_code}"
    except Exception as e:
        last_error = str(e)

    # Tentativa 2: fallback para curl (suporta HTTP/2 nativo e ignora restrições do Azure Front Door WAF)
    try:
        curl_bin = 'curl.exe' if sys.platform == 'win32' else 'curl'
        cmd = [
            curl_bin, '-s', '-L', '-k', '--http2',
            '-H', f"User-Agent: {HTTP_HEADERS['User-Agent']}",
            '-H', f"Accept: {HTTP_HEADERS['Accept']}",
            '-H', f"Accept-Language: {HTTP_HEADERS['Accept-Language']}",
            url
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and len(res.stdout) > 100:
            return json.loads(res.stdout) if is_json else res.stdout
    except Exception as e:
        last_error = f"requests: {last_error}; curl: {e}"

    raise Exception(last_error or f"Falha ao aceder a {url}")

def send_telegram_raw(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

    if not token or not chat_id:
        print("[AVISO] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        res = r.json()
        if res.get("ok"):
            print(f"[Telegram] Mensagem enviada com sucesso ({len(text)} carateres).")
            return True
        else:
            print(f"[Telegram Erro HTML] {res.get('description')}. Tentando sem formatação HTML...")
            payload.pop("parse_mode", None)
            r2 = requests.post(url, json=payload, timeout=15)
            return r2.json().get("ok", False)
    except Exception as e:
        print(f"[Telegram Exceção] {e}")
        return False

def send_telegram_messages(chunks):
    success = True
    for chunk in chunks:
        ok = send_telegram_raw(chunk)
        if not ok:
            success = False
    return success

# ==============================================================================
# PARSERS DAS ENTIDADES DE SAÚDE
# ==============================================================================

def fetch_portal_engine(base_domain, entity_name, sections=None):
    if sections is None:
        sections = ["processos-a-decorrer", "processos-ativos", "processos-abertos"]
        
    contests = []
    seen_ids = set()

    for section in sections:
        page = 1
        max_pages = 8
        while page <= max_pages:
            url = f"{base_domain}/{section}" if page == 1 else f"{base_domain}/{section}?recruitment_processes_list_3_page={page}"
            try:
                html_text = robust_fetch(url)
                soup = BeautifulSoup(html_text, 'html.parser')
                links = soup.find_all('a', href=re.compile(r'recruitment_process_id=(\d+)'))
                if not links:
                    break

                new_on_page = 0
                for a in links:
                    href = a['href']
                    m = re.search(r'recruitment_process_id=(\d+)', href)
                    proc_id = m.group(1) if m else href

                    if proc_id in seen_ids:
                        continue
                    seen_ids.add(proc_id)

                    title = a.get_text(' ', strip=True)
                    full_url = urljoin(base_domain, href)

                    parent = a.find_parent(['div', 'tr', 'li']) or a
                    details = parent.get_text(' ', strip=True)

                    contests.append({
                        'id': f"{entity_name}_{proc_id}",
                        'title': title,
                        'details': details[:300],
                        'url': full_url,
                        'section': section
                    })
                    new_on_page += 1

                next_page = soup.find('a', href=re.compile(rf'page={page+1}'))
                if not next_page or new_on_page == 0:
                    break
                page += 1
            except Exception:
                break

    return contests

def parse_wordpress_api(base_domain, category_slug=None):
    """
    Fallback universal para portais WordPress do SNS (utiliza a API REST oficial /wp-json/wp/v2/posts)
    """
    url = f"{base_domain}/wp-json/wp/v2/posts?per_page=35"
    data = robust_fetch(url, is_json=True)
    contests = []
    
    if isinstance(data, list):
        for post in data:
            title_rendered = post.get('title', {}).get('rendered', '')
            # Decode HTML entities
            title = html.unescape(BeautifulSoup(title_rendered, 'html.parser').get_text())
            link = post.get('link', '')
            excerpt_rendered = post.get('excerpt', {}).get('rendered', '')
            excerpt = html.unescape(BeautifulSoup(excerpt_rendered, 'html.parser').get_text())
            
            contests.append({
                'id': link or f"{base_domain}_{post.get('id')}",
                'title': title,
                'details': excerpt[:300],
                'url': link
            })
    return contests

def parse_ulsam():
    url = "https://www.ulsam.min-saude.pt/category/profissional/concursos/"
    try:
        html_text = robust_fetch(url)
        soup = BeautifulSoup(html_text, 'html.parser')
        contests = []
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/profissional/' in href and not any(skip in href.lower() for skip in ['/category/', '/tag/', 'page/']):
                title = a.get_text(' ', strip=True)
                if len(title) > 15 and not any(skip in title.lower() for skip in ['limpar filtros', 'área do profissional', 'concursos / recrutamento']):
                    full_url = urljoin(url, href)
                    parent = a.find_parent(['article', 'div', 'li']) or a
                    contests.append({
                        'id': full_url,
                        'title': title,
                        'details': parent.get_text(' ', strip=True)[:300],
                        'url': full_url
                    })
        if contests:
            return contests
    except Exception:
        pass
    # Fallback para WP REST API
    return parse_wordpress_api("https://www.ulsam.min-saude.pt")

def parse_uls_braga():
    return fetch_portal_engine("https://recrutamento.hospitaldebraga.pt", "Braga")

def parse_ulsbe():
    url = "https://www.ulsbe.min-saude.pt/2018/01/16/concursos-institucionais/"
    try:
        html_text = robust_fetch(url)
        soup = BeautifulSoup(html_text, 'html.parser')
        contests = []
        
        content = soup.find('div', class_=re.compile(r'entry-content|post-content')) or soup
        for el in content.find_all(['p', 'li', 'h4', 'h5', 'h3']):
            text = el.get_text(' ', strip=True)
            if any(w in text.lower() for w in ['procedimento', 'concurso', 'reserva', 'manifestação', 'aviso', 'recrutamento']):
                if len(text) > 20 and not any(skip in text.lower() for skip in ['cookies', 'informação clínica', 'informação legal']):
                    a = el.find('a', href=True)
                    link = urljoin(url, a['href']) if a else url
                    contests.append({
                        'id': f"ULSBE_{hash(text[:80])}",
                        'title': text[:200],
                        'details': text[:350],
                        'url': link
                    })
        if contests:
            return contests
    except Exception:
        pass
    return parse_wordpress_api("https://www.ulsbe.min-saude.pt")

def parse_uls_alto_ave():
    url = "https://www.ulsaave.min-saude.pt/category/recrutamento/"
    try:
        html_text = robust_fetch(url)
        soup = BeautifulSoup(html_text, 'html.parser')
        contests = []
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            if re.search(r'/\d{4}/\d{2}/', href):
                title = a.get_text(' ', strip=True)
                if len(title) > 10:
                    parent = a.find_parent(['article', 'div', 'li']) or a
                    contests.append({
                        'id': href,
                        'title': title,
                        'details': parent.get_text(' ', strip=True)[:300],
                        'url': urljoin(url, href)
                    })
        if contests:
            return contests
    except Exception:
        pass
    return parse_wordpress_api("https://www.ulsaave.min-saude.pt")

def parse_uls_medio_ave():
    return fetch_portal_engine("https://recrutamento.chma.min-saude.pt", "CHMA")

def parse_uls_povoa_vila_conde():
    return fetch_portal_engine("https://recrutamento.ulspvvc.min-saude.pt", "PVVC")

def parse_uls_sao_joao():
    return fetch_portal_engine("https://concursosrh-ulssjoao.min-saude.pt", "SaoJoao", sections=["processos-abertos", "processos-a-decorrer"])

def parse_uls_santo_antonio():
    url = "https://www.ulssa.pt/recrutamento/tecnicos-superiores"
    html_text = robust_fetch(url)
    soup = BeautifulSoup(html_text, 'html.parser')
    contests = []
    
    cards = soup.find_all('div', class_='card')
    for c in cards:
        header = c.find(class_='card-header')
        body = c.find(class_='card-body')
        head_text = header.get_text(' ', strip=True) if header else ""
        body_text = body.get_text(' ', strip=True) if body else ""
        
        title = head_text or body_text[:120]
        a = c.find('a', href=True)
        link = urljoin(url, a['href']) if a else url
        
        contests.append({
            'id': f"ULSSA_{hash(title[:60])}",
            'title': title[:180],
            'details': (head_text + " " + body_text)[:350],
            'url': link
        })
    return contests

def parse_uls_matosinhos():
    url = "https://www.ulsm.min-saude.pt/category/profissional/concursos/"
    try:
        html_text = robust_fetch(url)
        soup = BeautifulSoup(html_text, 'html.parser')
        contests = []
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/profissional/concursos/' in href and href.rstrip('/') != url.rstrip('/'):
                title = a.get_text(' ', strip=True)
                if len(title) > 10 and not any(skip in title.lower() for skip in ['limpar filtros', 'concursos / recrutamento']):
                    parent = a.find_parent(['article', 'div', 'li']) or a
                    contests.append({
                        'id': href,
                        'title': title,
                        'details': parent.get_text(' ', strip=True)[:300],
                        'url': urljoin(url, href)
                    })
        if contests:
            return contests
    except Exception:
        pass
    return parse_wordpress_api("https://www.ulsm.min-saude.pt")

def parse_uls_gaia_espinho():
    return fetch_portal_engine("https://recrutamento.ulsge.min-saude.pt", "ULSGE")

def parse_spms():
    api_url = "https://webrhv.min-saude.pt/recrutamento/api/v1/anuncios/instituicoes/MTMzMA"
    contests = []
    
    try:
        data = robust_fetch(api_url, is_json=True)
        if isinstance(data, list):
            for item in data:
                ref = item.get('REFERENCIA', '')
                tit = item.get('TITULO', '')
                dt_fim = item.get('DAT_FIM', '')
                anuncio_id = item.get('ID_ANUNCIOS', '')
                
                contests.append({
                    'id': f"SPMS_{anuncio_id}_{ref}",
                    'title': f"[{ref}] {tit}" if ref else tit,
                    'details': f"Data limite candidatura: {dt_fim}",
                    'url': "https://www.spms.min-saude.pt/recrutamento/"
                })
            return contests
    except Exception:
        pass
        
    # Fallback para raspagem direta da página oficial SPMS
    html_spms = robust_fetch("https://www.spms.min-saude.pt/recrutamento/")
    soup = BeautifulSoup(html_spms, 'html.parser')
    for a in soup.find_all('a', href=True):
        t = a.get_text(' ', strip=True)
        if any(w in t.lower() for w in ['concurso', 'recrutamento', 'procedimento', 'vaga']):
            contests.append({
                'id': f"SPMS_{hash(t)}",
                'title': t,
                'details': t,
                'url': urljoin("https://www.spms.min-saude.pt", a['href'])
            })
    return contests

ENTITIES = [
    {
        "name": "ULS do Alto Minho (ULSAM)",
        "url": "https://www.ulsam.min-saude.pt/category/profissional/concursos/",
        "parser": parse_ulsam
    },
    {
        "name": "ULS de Braga (Hospital de Braga)",
        "url": "https://recrutamento.hospitaldebraga.pt/processos-a-decorrer",
        "parser": parse_uls_braga
    },
    {
        "name": "ULS de Barcelos/Esposende (ULSBE)",
        "url": "https://www.ulsbe.min-saude.pt/2018/01/16/concursos-institucionais/",
        "parser": parse_ulsbe
    },
    {
        "name": "ULS do Alto Ave (Guimarães e Fafe)",
        "url": "https://www.ulsaave.min-saude.pt/category/recrutamento/",
        "parser": parse_uls_alto_ave
    },
    {
        "name": "ULS do Médio Ave (Famalicão e Santo Tirso)",
        "url": "https://recrutamento.chma.min-saude.pt/processos-a-decorrer",
        "parser": parse_uls_medio_ave
    },
    {
        "name": "ULS da Póvoa de Varzim/Vila do Conde",
        "url": "https://recrutamento.ulspvvc.min-saude.pt/processos-a-decorrer",
        "parser": parse_uls_povoa_vila_conde
    },
    {
        "name": "ULS de São João (Porto e Valongo)",
        "url": "https://concursosrh-ulssjoao.min-saude.pt/processos-abertos",
        "parser": parse_uls_sao_joao
    },
    {
        "name": "ULS de Santo António (ULSSA)",
        "url": "https://www.ulssa.pt/recrutamento/tecnicos-superiores",
        "parser": parse_uls_santo_antonio
    },
    {
        "name": "ULS de Matosinhos (ULSM)",
        "url": "https://www.ulsm.min-saude.pt/category/profissional/concursos/",
        "parser": parse_uls_matosinhos
    },
    {
        "name": "ULS de Gaia/Espinho (ULSGE)",
        "url": "https://recrutamento.ulsge.min-saude.pt/processos-a-decorrer",
        "parser": parse_uls_gaia_espinho
    },
    {
        "name": "SPMS",
        "url": "https://www.spms.min-saude.pt/recrutamento/",
        "parser": parse_spms
    }
]

# ==============================================================================
# GESTÃO DE ESTADO E FORMATAÇÃO DE ALERTAS
# ==============================================================================

def load_state(state_file):
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"known_matches": {}, "last_run": None}
    return {"known_matches": {}, "last_run": None}

def save_state(state_file, state):
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def build_telegram_chunks(matches_by_entity):
    total = sum(len(m_list) for m_list in matches_by_entity.values())
    now_str = datetime.now().strftime("%d/%m/%Y às %H:%M")
    
    header = (
        f"🚨 <b>ALERTA DE CONCURSOS PÚBLICOS DE SAÚDE</b>\n"
        f"📅 <i>Verificação semanal: {now_str}</i>\n"
        f"🎯 Termos: <b>Nutrição | Informática | Informação</b>\n\n"
        f"Encontrados <b>{total}</b> concurso(s) relevante(s) em vigor:\n"
    )
    
    chunks = []
    current_chunk = header
    
    for entity_name, matches in matches_by_entity.items():
        block = [f"\n🏥 <b><u>{html.escape(entity_name)}</u></b> ({len(matches)}):"]
        for m in matches:
            kw_badge = f"🏷️ <code>[{m['keyword']}]</code>"
            is_new = " 🆕" if m.get("is_new") else ""
            title_esc = html.escape(m['title'])
            block.append(f"• {kw_badge}{is_new} <b>{title_esc}</b>")
            if m.get('details') and len(m['details']) > len(m['title']) + 10:
                clean_det = html.escape(m['details'][:140])
                block.append(f"  <i>{clean_det}...</i>")
            block.append(f"  🔗 <a href=\"{m['url']}\">Aceder ao Concurso / Edital</a>")
            
        block_text = "\n".join(block) + "\n"
        
        if len(current_chunk) + len(block_text) > 3800:
            chunks.append(current_chunk)
            current_chunk = block_text
        else:
            current_chunk += block_text
            
    if current_chunk.strip():
        chunks.append(current_chunk)
        
    return chunks

def format_telegram_error_alert(errors):
    now_str = datetime.now().strftime("%d/%m/%Y às %H:%M")
    lines = [
        f"⚠️ <b>ALERTA DE ERRO NO ACESSO A CONCURSOS</b>",
        f"📅 <i>Data: {now_str}</i>\n",
        f"O agente não conseguiu aceder ou analisar {len(errors)} portal(is) de saúde:\n"
    ]
    for err in errors:
        lines.append(f"❌ <b>{html.escape(err['name'])}</b>")
        lines.append(f"   Erro: <code>{html.escape(str(err['error']))}</code>")
        lines.append(f"   URL: <a href=\"{err['url']}\">{err['url']}</a>\n")
    lines.append("Verifique se os respetivos servidores se encontram em manutenção.")
    return "\n".join(lines)

def run_check(dry_run=False, force_alert=False, send_summary=False):
    print("=" * 70)
    print("AGENTE DE MONITORIZAÇÃO DE CONCURSOS DE SAÚDE")
    print(f"Data/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "concursos_state.json")
    state = load_state(state_file)
    known_matches = state.get("known_matches", {})
    
    all_matches_by_entity = {}
    new_matches_count = 0
    errors = []
    
    for entity in ENTITIES:
        name = entity["name"]
        url = entity["url"]
        parser = entity["parser"]
        print(f"\nA verificar: {name}...")
        
        try:
            contests = parser()
            print(f"  ✓ {len(contests)} concurso(s)/processo(s) extraído(s).")
            
            matched_for_entity = []
            for c in contests:
                full_text = f"{c['title']} {c.get('details', '')}"
                is_match, kw = check_keyword_match(full_text)
                if is_match:
                    cid = c.get('id', c['url'])
                    is_new = cid not in known_matches
                    if is_new:
                        new_matches_count += 1
                        known_matches[cid] = {
                            "entity": name,
                            "title": c['title'],
                            "keyword": kw,
                            "url": c['url'],
                            "first_seen": datetime.now().isoformat()
                        }
                    
                    matched_for_entity.append({
                        "title": c['title'],
                        "details": c.get('details', ''),
                        "url": c['url'],
                        "keyword": kw,
                        "is_new": is_new
                    })
                    print(f"    ⭐ MATCH [{kw}]: {c['title'][:70]} (Novo: {is_new})")
            
            if matched_for_entity:
                all_matches_by_entity[name] = matched_for_entity
                
        except Exception as e:
            err_msg = str(e)
            print(f"  ❌ ERRO ao aceder/analisar {name}: {err_msg}")
            errors.append({
                "name": name,
                "url": url,
                "error": err_msg
            })
            
    # Salvar estado
    state["last_run"] = datetime.now().isoformat()
    state["known_matches"] = known_matches
    if not dry_run:
        save_state(state_file, state)
        
    print("\n" + "=" * 70)
    print("RESUMO DA VERIFICAÇÃO:")
    print(f"Entidades com erros: {len(errors)} / {len(ENTITIES)}")
    total_found = sum(len(v) for v in all_matches_by_entity.values())
    print(f"Total de concursos relevantes em vigor: {total_found} (Novos: {new_matches_count})")
    print("=" * 70)
    
    # 1. Enviar Alerta de Erros se algum portal falhou
    if errors:
        error_msg = format_telegram_error_alert(errors)
        print("\n--- Alerta de Erro Gerado ---")
        print(error_msg)
        if not dry_run:
            send_telegram_raw(error_msg)
            
    # 2. Enviar Alerta de Concursos se encontrados
    if all_matches_by_entity and (new_matches_count > 0 or send_summary or force_alert):
        chunks = build_telegram_chunks(all_matches_by_entity)
        print(f"\n--- Mensagem de Alerta ({len(chunks)} parte(s)) ---")
        for idx, chunk in enumerate(chunks):
            print(f"\n[PARTE {idx+1}/{len(chunks)}]:\n{chunk}")
        if not dry_run:
            send_telegram_messages(chunks)
    elif force_alert:
        test_msg = (
            f"ℹ️ <b>TESTE DO AGENTE DE CONCURSOS</b>\n\n"
            f"O agente foi executado com sucesso.\n"
            f"Concursos ativos em vigor: {total_found}.\n"
            f"Próxima verificação automática: Domingo às 14:44."
        )
        if not dry_run:
            send_telegram_raw(test_msg)
    else:
        if total_found == 0:
            print("\nNenhum concurso com os termos 'nutrição', 'informática' ou 'informação' encontrado.")
        else:
            print(f"\n{total_found} concursos em vigor encontrados (todos já conhecidos anteriormente).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verificador de concursos públicos de saúde")
    parser.add_argument("--dry-run", action="store_true", help="Executa o scraper sem enviar mensagens nem gravar estado")
    parser.add_argument("--force-alert", action="store_true", help="Força o envio da mensagem de alerta")
    parser.add_argument("--summary", action="store_true", help="Envia o resumo semanal de todos os concursos em vigor")
    parser.add_argument("--schedule-check", action="store_true", help="Verifica se a hora local em Lisboa é 13h antes de executar")
    args = parser.parse_args()
    
    if args.schedule_check:
        try:
            from zoneinfo import ZoneInfo
            lisbon_hour = datetime.now(ZoneInfo("Europe/Lisbon")).hour
            if lisbon_hour != 14:
                print(f"[Agendamento] Hora em Lisboa é {lisbon_hour}h (não é 14h). A ignorar esta passagem.")
                sys.exit(0)
            print(f"[Agendamento] Confirmada hora de Lisboa: {lisbon_hour}h. A iniciar verificação...")
        except Exception as e:
            print(f"[Agendamento] Aviso no cálculo de fuso horário: {e}. Prosseguindo com a verificação.")

    send_summary = True if args.summary or not (args.dry_run or args.force_alert) else False
    run_check(dry_run=args.dry_run, force_alert=args.force_alert, send_summary=send_summary)
