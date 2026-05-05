#!/usr/bin/env python3
"""
ONM Data Scraper - rulează de pe PC-ul tău personal (nu de pe server cloud).
Descarcă rezultatele Olimpiadei Naționale de Matematică de pe ssmr.ro.

Instalare dependențe:
    pip install requests pdfplumber beautifulsoup4

Utilizare:
    python fetch_onm.py                    # toate clasele și toți anii lipsă
    python fetch_onm.py --year 2015        # doar 2015
    python fetch_onm.py --year 2015 --cls 9 10 11 12

Notă: ssmr.ro blochează IP-urile de cloud/VPN - rulează de pe conexiune normală (casă/facultate).
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
    "Referer": "https://ssmr.ro/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

OUT_FILE = Path(__file__).parent.parent / "onm_all.json"

# ─── URL patterns ────────────────────────────────────────────────────────────

def pdf_url(year: int, cls: int) -> str:
    """URL-ul PDF-ului cu rezultate finale pentru un an și o clasă."""
    return (
        f"https://ssmr.ro/files/onm{year}/faza_nationala/rezultate/"
        f"cl{cls}_nationala_final.pdf"
    )

ONM2015_RESULTS = "https://onm2015.ssmr.ro/rezultate"

# ─── PDF parsing ─────────────────────────────────────────────────────────────

PREMIU_RE = re.compile(
    r"(PREMIUL\s+[I]{1,3}V?|MENȚIUNE|MENTIUNE|MENTION)", re.IGNORECASE
)
MEDALIE_RE = re.compile(r"(AUR|ARGINT|BRONZ)", re.IGNORECASE)


def parse_pdf_bytes(pdf_bytes: bytes, cls: int) -> list[dict]:
    """Parsează un PDF de rezultate ONM și returnează lista de elevi premiați."""
    results = []
    import io

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row:
                        continue
                    # Filtrăm rândurile fără conținut util
                    cells = [str(c).strip() if c else "" for c in row]
                    text = " ".join(cells)
                    if not PREMIU_RE.search(text) and not MEDALIE_RE.search(text):
                        continue
                    # Extragem câmpurile
                    entry = _extract_row(cells, cls)
                    if entry:
                        results.append(entry)
    return results


def _extract_row(cells: list[str], cls: int) -> dict | None:
    """Încearcă să extragă un entry din celulele unui rând de tabel."""
    # Căutăm premiu/medalie
    premiu = None
    medalie = None
    for c in cells:
        pm = PREMIU_RE.search(c)
        mm = MEDALIE_RE.search(c)
        if pm and not premiu:
            premiu = pm.group(1).upper().strip()
        if mm and not medalie:
            medalie = mm.group(1).upper().strip()

    if not premiu and not medalie:
        return None

    # Primul câmp nevid care nu e număr = numele
    nume = None
    judet = None
    punctaj = None

    for c in cells:
        c = c.strip()
        if not c:
            continue
        # Punctaj
        try:
            v = float(c.replace(",", "."))
            if 0 <= v <= 30:
                punctaj = v
            continue
        except ValueError:
            pass
        # Dacă arată ca un județ cunoscut
        if _looks_like_judet(c) and not judet:
            judet = c
            continue
        # Altfel, probabil nume
        if not nume and len(c) > 3 and not PREMIU_RE.search(c) and not MEDALIE_RE.search(c):
            nume = c

    if not nume:
        return None

    return {
        "nume": _norm_name(nume),
        "judet": judet or "",
        "punctaj": punctaj,
        "premiu": premiu,
        "medalieSSMR": medalie,
        "clasa": str(cls),
    }


JUDETE = {
    "ALBA", "ARAD", "ARGEȘ", "ARGES", "ARGEŞ", "BACĂU", "BACAU",
    "BIHOR", "BISTRIȚA-NĂSĂUD", "BOTOȘANI", "BOTOSANI", "BRAȘOV",
    "BRAILA", "BRĂILA", "BUZĂU", "BUZAU", "CARAȘ-SEVERIN",
    "CĂLĂRAȘI", "CLUJ", "CONSTANȚA", "CONSTANTA", "COVASNA",
    "DÂMBOVIȚA", "DOLJ", "GALAȚI", "GALATI", "GIURGIU", "GORJ",
    "HARGHITA", "HUNEDOARA", "IALOMIȚA", "IAȘI", "IASI", "ILFOV",
    "MARAMUREȘ", "MARAMURES", "MEHEDINȚI", "MUREȘ", "MURES",
    "NEAMȚ", "NEAMT", "OLT", "PRAHOVA", "SĂLAJ", "SALAJ",
    "SATU MARE", "SIBIU", "SUCEAVA", "TELEORMAN", "TIMIȘ", "TIMIS",
    "TULCEA", "VÂLCEA", "VALCEA", "VASLUI", "VRANCEA",
    "BUCUREȘTI", "BUCURESTI", "MUNICIPIUL BUCUREȘTI",
}


def _looks_like_judet(s: str) -> bool:
    return s.upper() in JUDETE or s.upper().startswith("BUCUREȘTI")


def _norm_name(s: str) -> str:
    return " ".join(s.upper().split())


# ─── 2015 special: HTML scraping ─────────────────────────────────────────────

def fetch_2015(classes: list[int]) -> dict:
    """Scrape onm2015.ssmr.ro/rezultate pentru clasele date."""
    year_data: dict[str, list] = {}

    for cls in classes:
        print(f"  Clasa {cls}...", end=" ", flush=True)
        entries = []
        # Pagina filtrată pe clasă
        url = f"{ONM2015_RESULTS}?clasa={cls}"
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"EROARE: {e}")
            year_data[str(cls)] = []
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        # Căutăm tabelele cu rezultate
        tables = soup.find_all("table")
        for table in tables:
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not cells:
                    continue
                text = " ".join(cells)
                if not PREMIU_RE.search(text) and not MEDALIE_RE.search(text):
                    continue
                entry = _extract_row(cells, cls)
                if entry:
                    entries.append(entry)

        print(f"{len(entries)} entries")
        year_data[str(cls)] = entries

    return year_data


# ─── Generic PDF fetcher ──────────────────────────────────────────────────────

def fetch_year_pdf(year: int, classes: list[int]) -> dict:
    """Descarcă și parsează PDF-urile pentru un an dat."""
    year_data: dict[str, list] = {}

    for cls in classes:
        print(f"  Clasa {cls}...", end=" ", flush=True)
        url = pdf_url(year, cls)
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
        except requests.HTTPError as e:
            print(f"HTTP {e.response.status_code} - sărit")
            year_data[str(cls)] = []
            continue
        except Exception as e:
            print(f"EROARE: {e} - sărit")
            year_data[str(cls)] = []
            continue

        entries = parse_pdf_bytes(r.content, cls)
        print(f"{len(entries)} entries")
        year_data[str(cls)] = entries
        time.sleep(1)  # politicos față de server

    return year_data


# ─── Older years (2002-2009): ssmr.ro/arhiva ─────────────────────────────────

def fetch_archive_index() -> list[str]:
    """Returnează toate URL-urile de PDF din pagina de arhivă ssmr.ro/arhiva."""
    try:
        r = SESSION.get("https://ssmr.ro/arhiva", timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"Eroare la arhivă: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "rezultat" in href.lower() and href.endswith(".pdf"):
            if not href.startswith("http"):
                href = "https://ssmr.ro" + href
            links.append(href)
    return links


def year_from_url(url: str) -> int | None:
    m = re.search(r"20\d{2}", url)
    if m:
        return int(m.group())
    return None


def cls_from_url(url: str) -> int | None:
    m = re.search(r"cl(?:asa)?[-_]?(\d{1,2})", url, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ─── Main ─────────────────────────────────────────────────────────────────────

def load_existing() -> dict:
    if OUT_FILE.exists():
        with open(OUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(data: dict) -> None:
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Salvat în {OUT_FILE}")


def main():
    parser = argparse.ArgumentParser(description="ONM Scraper")
    parser.add_argument("--year", type=int, nargs="+", help="Ani de descărcat (e.g. 2015 2022)")
    parser.add_argument("--cls", type=int, nargs="+", default=list(range(5, 13)),
                        help="Clase (default: 5-12)")
    parser.add_argument("--archive", action="store_true",
                        help="Caută și în pagina de arhivă ssmr.ro/arhiva")
    args = parser.parse_args()

    data = load_existing()

    # Ani lipsă din baza de date
    ALL_YEARS = list(range(2002, 2027))
    # 2020-2021 nu au avut ONM (COVID)
    NO_ONM_YEARS = {2020, 2021}

    target_years = args.year or [
        y for y in ALL_YEARS
        if str(y) not in data and y not in NO_ONM_YEARS
    ]

    print(f"Ani de descărcat: {target_years}")
    print(f"Clase: {args.cls}")

    for year in target_years:
        if year in NO_ONM_YEARS:
            print(f"\n{year}: Nu s-a organizat ONM (COVID-19). Sărit.")
            continue

        print(f"\n=== {year} ===")

        if year == 2015:
            # Site dedicat cu HTML
            year_data = fetch_2015(args.cls)
        else:
            # Fișiere PDF de pe ssmr.ro
            year_data = fetch_year_pdf(year, args.cls)

        # Filtrăm doar clasele cu date
        year_data = {k: v for k, v in year_data.items() if v}

        if year_data:
            data[str(year)] = {"an": year, "clase": year_data}
            save(data)
        else:
            print(f"  Nu s-au găsit date pentru {year}.")

    if args.archive:
        print("\n=== Arhivă veche (ssmr.ro/arhiva) ===")
        pdf_links = fetch_archive_index()
        print(f"Găsite {len(pdf_links)} PDF-uri în arhivă.")
        for url in pdf_links:
            yr = year_from_url(url)
            cls = cls_from_url(url)
            if not yr or not cls:
                continue
            if str(yr) in data:
                continue
            print(f"  {yr}/cl{cls}: {url}")
            try:
                r = SESSION.get(url, timeout=30)
                r.raise_for_status()
                entries = parse_pdf_bytes(r.content, cls)
                if entries:
                    if str(yr) not in data:
                        data[str(yr)] = {"an": yr, "clase": {}}
                    data[str(yr)]["clase"][str(cls)] = entries
                    print(f"    {len(entries)} entries salvate.")
                    save(data)
                time.sleep(1)
            except Exception as e:
                print(f"    Eroare: {e}")

    print("\nGata!")


if __name__ == "__main__":
    main()
