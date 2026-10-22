#!/usr/bin/env python3
"""Fetch the latest IMD Met Centre Bhubaneswar bulletins and write weather.json.

Sources (public PDFs from mausam.imd.gov.in):
  midday.pdf   - district forecast and warnings for the next 7 days
  temprain.pdf - 8:30 am station observations

Run:  python scripts/update_weather.py [--force] [--min-hours 36]
      python scripts/update_weather.py --midday a.pdf --temprain b.pdf   (local test)

The script never overwrites weather.json with a broken parse: if the bulletin
can't be read it exits with an error and leaves the previous file in place.
"""
import argparse, datetime as dt, io, json, os, re, sys, urllib.request

import pdfplumber

BASE = "https://mausam.imd.gov.in/bhubaneswar/mcdata/"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weather.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Map names (as used on the district map) and the spellings IMD uses for them
DISTRICTS = {
    "Anugola": ["Anugola", "Anugul", "Angul"],
    "Balangir": ["Balangir", "Bolangir"],
    "Baleshwar": ["Baleshwar", "Balasore", "Baleswar"],
    "Baragada": ["Baragada", "Bargarh"],
    "Bhadrak": ["Bhadrak"],
    "Boudh": ["Boudh", "Baudh"],
    "Debagada": ["Debagada", "Deogarh", "Debagarh"],
    "Dhenkanal": ["Dhenkanal"],
    "Gajapati": ["Gajapati"],
    "Ganjam": ["Ganjam"],
    "Jagatsinghapur": ["Jagatsinghapur", "Jagatsinghpur"],
    "Jajapur": ["Jajapur", "Jajpur"],
    "Jharsuguda": ["Jharsuguda"],
    "Kalahandi": ["Kalahandi"],
    "Kandhamala": ["Kandhamala", "Kandhamal"],
    "Kataka": ["Kataka", "Cuttack"],
    "Kendrapada": ["Kendrapada", "Kendrapara"],
    "Kendujhar": ["Kendujhar", "Keonjhar"],
    "Khordha": ["Khordha", "Khurda", "Khurdha"],
    "Koraput": ["Koraput"],
    "Malkangiri": ["Malkangiri", "Malkanagiri"],
    "Mayurbhanj": ["Mayurbhanj"],
    "Nabarangpur": ["Nabarangpur", "Nawarangpur", "Nowrangpur"],
    "Nayagada": ["Nayagada", "Nayagarh"],
    "Nuapada": ["Nuapada"],
    "Puri": ["Puri"],
    "Rayagada": ["Rayagada"],
    "Sambalpur": ["Sambalpur"],
    "Subarnapur": ["Subarnapur", "Sonepur", "Sonapur"],
    "Sundaragada": ["Sundaragada", "Sundargarh"],
}
_alt = sorted(((a, k) for k, v in DISTRICTS.items() for a in v), key=lambda x: -len(x[0]))
DIST_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(a) for a, _ in _alt) + r")(?![a-z])", re.I)
ALIAS = {a.lower(): k for a, k in _alt}

# 8:30 am observation stations -> district (first station listed is shown first)
STATIONS = {
    "BALESHWAR": ("Baleshwar", "Baleshwar"), "CHANDABALI": ("Bhadrak", "Chandbali"), "CHANDBALI": ("Bhadrak", "Chandbali"),
    "BHADRAK": ("Bhadrak", "Bhadrak"), "KATAKA": ("Kataka", "Cuttack"), "PARADIP": ("Jagatsinghapur", "Paradip"),
    "JAGATSINGHPUR": ("Jagatsinghapur", "Jagatsinghpur"), "BHUBANESWAR": ("Khordha", "Bhubaneswar"),
    "KHORDHA": ("Khordha", "Khordha"), "PURI": ("Puri", "Puri"), "CHATRAPUR": ("Ganjam", "Chhatrapur"),
    "GOPALPUR": ("Ganjam", "Gopalpur"), "ANUGOLA": ("Anugola", "Angul"), "TALACHER": ("Anugola", "Talcher"),
    "BARIPADA": ("Mayurbhanj", "Baripada"), "JHARSUGUDA": ("Jharsuguda", "Jharsuguda"),
    "KENDUJHAR": ("Kendujhar", "Keonjhar"), "KENDUJHARGADA": ("Kendujhar", "Keonjhar"),
    "SAMBALPUR": ("Sambalpur", "Sambalpur"), "HIRAKUD": ("Sambalpur", "Hirakud"),
    "SUNDARAGADA": ("Sundaragada", "Sundargarh"), "RAURKELA": ("Sundaragada", "Rourkela"),
    "BHABANIPATANA": ("Kalahandi", "Bhawanipatna"), "BALANGIR": ("Balangir", "Balangir"),
    "TITILAGARH": ("Balangir", "Titlagarh"), "TITLAGARH": ("Balangir", "Titlagarh"),
    "KORAPUT": ("Koraput", "Koraput"), "PHULABANI": ("Kandhamala", "Phulbani"),
    "DARINGIBADI": ("Kandhamala", "Daringibadi"), "MALKANGIRI": ("Malkangiri", "Malkangiri"),
    "SONPUR": ("Subarnapur", "Sonepur"), "NAYAGADA": ("Nayagada", "Nayagarh"), "BOUDH": ("Boudh", "Boudh"),
    "PARALAKHEMUNDI": ("Gajapati", "Paralakhemundi"), "RAYAGADA": ("Rayagada", "Rayagada"),
    "JAJPUR": ("Jajapur", "Jajpur"), "DHENKANAL": ("Dhenkanal", "Dhenkanal"), "BARAGADA": ("Baragada", "Bargarh"),
    "BARGADA": ("Baragada", "Bargarh"), "DEBAGADA": ("Debagada", "Deogarh"), "NUAPADA": ("Nuapada", "Nuapada"),
    "NAWARANGPUR": ("Nabarangpur", "Nawarangpur"), "KENDRAPADA": ("Kendrapada", "Kendrapada"),
}
RAIN_RANK = {"one": 1, "few": 2, "many": 3, "most": 4}
LEVEL_RANK = {"yellow": 1, "orange": 2, "red": 3}


def fetch(name):
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "Mozilla/5.0 (FLN dashboard weather updater)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def clean(t):
    return re.sub(r"\s+", " ", t or "").strip()


def ascii_part(t):
    """Keep only mostly-English lines (bulletins mix English, Hindi and Odia)."""
    keep = []
    for line in (t or "").splitlines():
        s = line.strip()
        if s and sum(ord(c) < 128 for c in s) / len(s) > 0.85:
            keep.append(s)
    return clean(" ".join(keep))


def names_in(text):
    found = []
    for m in DIST_RE.finditer(text):
        k = ALIAS[m.group(1).lower()]
        if k not in found:
            found.append(k)
    if not found and re.search(r"districts? of (the )?Odisha", text, re.I):
        found = list(DISTRICTS)
    return found


# ---------------------------------------------------------------- midday.pdf
def _lines(words, tol=4):
    """Group words into visual lines (tolerant of small baseline shifts between fonts)."""
    out = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1]["top"]) <= tol:
            out[-1]["words"].append(w)
        else:
            out.append({"top": w["top"], "words": [w]})
    for ln in out:
        ln["words"].sort(key=lambda w: w["x0"])
    return out


def _header_x(pdf):
    for page in pdf.pages:
        ws = page.extract_words()
        fx = next((w["x0"] for w in ws if re.match(r"^Forecast", w["text"]) and w["x0"] > 60), None)
        if fx is None:
            continue
        wx = next((w["x0"] for w in ws if re.match(r"^Warning", w["text"]) and w["x0"] > fx + 50), None)
        if wx is not None:
            return fx, wx
    return None, None


def words_columns(pdf):
    """Strategy 1: split words into the three table columns by x-position; rows start at each 'Day N'."""
    fx, wx = _header_x(pdf)
    if fx is None:
        return {}
    items = []  # (page, top, column, text)
    for pi, page in enumerate(pdf.pages):
        for ln in _lines(page.extract_words()):
            parts = {0: [], 1: [], 2: []}
            for w in ln["words"]:
                parts[0 if w["x0"] < fx - 8 else 1 if w["x0"] < wx - 8 else 2].append(w["text"])
            items.append((pi, ln["top"], {k: " ".join(v) for k, v in parts.items()}))
    days, cur = {}, None
    for pi, top, cols in items:
        m = re.match(r"^Day\s*(\d)\b", cols[0])
        if m:
            cur = int(m.group(1)); days.setdefault(cur, ["", ""])
        elif re.match(r"^(Outlook|Temperature)", cols[0]):
            cur = None
        if cur:
            days[cur][0] += " " + cols[1]
            days[cur][1] += " " + cols[2]
    return {k: tuple(v) for k, v in days.items()}


def rows_columns(pdf):
    """Strategy 2: like strategy 1, but use the table's ruled lines to find each row,
    so text that sits above a vertically-centred 'Day N' label still lands in the right day."""
    fx, wx = _header_x(pdf)
    if fx is None:
        return {}
    days, last = {}, None
    for page in pdf.pages:
        ys = sorted({round(e["top"]) for e in page.edges
                     if e.get("orientation") == "h" and e["x1"] - e["x0"] > (wx - fx)})
        if len(ys) < 2:
            continue
        bands = list(zip(ys, ys[1:]))
        words = page.extract_words()
        for i, (y0, y1) in enumerate(bands):
            cell = [w for w in words if y0 - 1 <= w["top"] < y1 - 1]
            c0 = " ".join(w["text"] for ln in _lines([w for w in cell if w["x0"] < fx - 8]) for w in ln["words"])
            c1 = " ".join(w["text"] for ln in _lines([w for w in cell if fx - 8 <= w["x0"] < wx - 8]) for w in ln["words"])
            c2 = " ".join(w["text"] for ln in _lines([w for w in cell if w["x0"] >= wx - 8]) for w in ln["words"])
            m = re.match(r"^Day\s*(\d)\b", c0)
            if m:
                last = int(m.group(1)); days[last] = [c1, c2]
            elif re.match(r"^(Outlook|Temperature|Days)", c0):
                last = None if not c0.startswith("Days") else last
            elif last and i == 0 and not c0:      # row continued from the previous page
                days[last][0] += " " + c1; days[last][1] += " " + c2
    return {k: tuple(v) for k, v in days.items()}


def grid_columns(pdf):
    """Strategy 3: pdfplumber's own table detection."""
    days, cur = {}, None
    for page in pdf.pages:
        for tb in page.extract_tables():
            for row in tb:
                cells = [clean(c) for c in row if c is not None]
                if len(cells) < 3:
                    continue
                m = re.match(r"^Day\s*(\d)", cells[0])
                if m:
                    cur = int(m.group(1))
                    days[cur] = [cells[-2], cells[-1]]
                elif re.match(r"^(Outlook|Temperature)", cells[0]):
                    cur = None
                elif cur and not cells[0]:
                    days[cur][0] += " " + cells[-2]
                    days[cur][1] += " " + cells[-1]
    return {k: tuple(v) for k, v in days.items()}


def layout_columns(pdf):
    """Strategy 4: plain-text layout; split each line at the forecast/warning column positions."""
    text = "\n".join((p.extract_text(layout=True) or "") for p in pdf.pages)
    lines = text.splitlines()
    starts = [ln.find("Light to") for ln in lines if "Light to" in ln]
    warns = [m.start() for ln in lines for m in [re.search(r"(YELLOW|ORANGE|RED)\s*WARNING|\bNIL\b", ln)] if m and m.start() > 20]
    if not starts or not warns:
        return {}
    f0 = max(set(starts), key=starts.count); w0 = max(set(warns), key=warns.count)
    days, cur = {}, None
    for ln in lines:
        c0, c1, c2 = ln[:f0], ln[f0:w0], ln[w0:]
        m = re.search(r"\bDay\s*(\d)\b", c0)
        if m:
            cur = int(m.group(1)); days.setdefault(cur, ["", ""])
        elif re.search(r"\b(Outlook|Temperature Forecast)", ln):
            cur = None
        if cur:
            days[cur][0] += " " + c1; days[cur][1] += " " + c2
    return {k: tuple(v) for k, v in days.items()}


def parse_forecast(f):
    f = clean(f)
    rain = {}
    pat = (r"(most|many|a\s*few|one\s*or\s*two)\s*places?\s*(?:over|in|of)\s*(?:the\s*)?districts?\s*(?:of\s*)?"
           r"(.*?)(?=;|\bat\s*(?:most|many|a\s*few|one\s*or\s*two)\b|\band\s*dry\b|\.\s|$)")
    for m in re.finditer(pat, f, re.I):
        key = re.sub(r"\s+", "", m.group(1).lower())
        cat = {"most": "most", "many": "many", "afew": "few", "oneortwo": "one"}[key]
        for d in names_in(m.group(2)):
            if RAIN_RANK[cat] > RAIN_RANK.get(rain.get(d), 0):
                rain[d] = cat
    return rain


def parse_warnings(w):
    w = clean(w)
    warn = {}
    segs = re.split(r"(RED|ORANGE|YELLOW)\s*WARNING", w, flags=re.I)
    for i in range(1, len(segs) - 1, 2):
        level = segs[i].lower()
        body = re.sub(r"^[:\-\s]*\([^)]*\)[:\-\s]*", "", segs[i + 1]).strip()
        for sent in re.split(r"(?<=\.)\s+(?=[A-Z])", body):
            ds = names_in(sent)
            if not ds:
                continue
            hz = re.split(r"\s*(?:is\s|are\s)?(?:very\s*|most\s*)?likely\b|\s+(?:at|over)\s*(?:one|a\s*few|many|most)\b", sent)[0]
            hz = clean(re.sub(r"\(\s*wind speed reaching\s+upto\s*", "(", hz, flags=re.I))[:160]
            for d in ds:
                cur = warn.setdefault(d, {"level": level, "hz": []})
                if LEVEL_RANK[level] > LEVEL_RANK[cur["level"]]:
                    cur["level"] = level
                if hz and hz not in cur["hz"]:
                    cur["hz"].append(hz)
    return warn


def parse_midday(data):
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        tries = []
        for name, fn in [("rows", rows_columns), ("grid", grid_columns), ("words", words_columns), ("layout", layout_columns)]:
            try:
                tries.append((name, fn(pdf)))
            except Exception as e:
                print(f"  strategy {name} failed: {e}")
    m = re.search(r"DATE:\s*(\d{2})\.(\d{2})\.(\d{4})", text)
    if not m:
        raise ValueError("bulletin date not found")
    bdate = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    tm = re.search(r"TIME OF ISSUE:\s*(\d{2})(\d{2})", text)

    def build(cols):
        return [{"date": (bdate + dt.timedelta(days=n - 1)).isoformat(),
                 "rain": parse_forecast(cols[n][0]), "warn": parse_warnings(cols[n][1])}
                for n in sorted(cols) if 1 <= n <= 7]

    best = None
    for name, cols in tries:
        days = build(cols) if cols else []
        mentioned = sum(len(names_in(cols[n][0])) + len(names_in(cols[n][1])) for n in cols if 1 <= n <= 7) if cols else 0
        parsed = sum(len(d["rain"]) + len(d["warn"]) for d in days)
        print(f"  strategy {name}: {len(days)} days, {parsed} of {mentioned} district mentions read")
        ok = len(days) >= 3 and (parsed >= mentioned * 0.5 or mentioned == 0)
        if ok and (best is None or parsed > best[2]):
            best = (name, days, parsed, cols)
    if best is None:
        print("---- Could not read the forecast table. What each strategy saw for Day 1: ----")
        for name, cols in tries:
            c = (cols or {}).get(1, ("", ""))
            print(f"[{name}] FORECAST: {clean(c[0])[:400]}")
            print(f"[{name}] WARNING : {clean(c[1])[:400]}")
        print("---- Start of the bulletin text: ----")
        print(text[:2500])
        raise ValueError("forecast table could not be read (details above)")
    name, days, _, cols = best
    print(f"  using strategy: {name}")
    for d in days:
        print(f"  {d['date']}: rain in {len(d['rain'])} districts, warnings in {len(d['warn'])}")

    def grab(p):
        mm = re.search(p, text, re.S | re.I)
        return mm.group(1) if mm else ""
    summary = {
        "synoptic": clean(ascii_part(grab(r"❖\s*(.*?)(?=❖|Summary of Observ)")))[:300],
        "observed": ascii_part(grab(r"Summary of Observation[^\n]*\n(?:[^\n]*:\)?\s*\n)?(.*?)(?=The chief amount|Maximum temperatures|Weather forecast and warning)"))[:400],
        "extremes": clean(grab(r"(The highest maximum temperature.*?Odisha\.)")),
        "bhubaneswar": ascii_part(grab(r"Forecast for Bhubaneswar[^\n]*\n(.*?)(?=Observations of Bhubaneswar)"))[:240],
        "fishermen": ascii_part(grab(r"Weather warning for fishermen[^\n]*\n(.*?)(?=N\.\s*B|Kindly download)")).replace("(", " (")[:240],
    }
    return {
        "kind": "Mid-day",
        "date": bdate.isoformat(),
        "time": f"{tm.group(1)}:{tm.group(2)}" if tm else "",
        "summary": summary,
        "days": days,
    }


# -------------------------------------------------------------- temprain.pdf
def parse_temprain(data):
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    m = re.search(r"Date\s*[–-]\s*(\d{2})\.(\d{2})\.(\d{4})", text)
    odate = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat() if m else None
    obs = {}
    for line in text.splitlines():
        mm = re.match(r"^\s*([A-Z]{3,})\b", line)
        if not mm or mm.group(1) not in STATIONS:
            continue
        nums = re.findall(r"(?<!\S)(-|TRACE|\d+(?:\.\d+)?)(?!\S)", line)[:4]
        nums += ["-"] * (4 - len(nums))
        val = lambda s, t=float: None if s == "-" else (0.0 if s == "TRACE" else t(float(s)))
        dist, label = STATIONS[mm.group(1)]
        obs.setdefault(dist, []).append([label, val(nums[0]), val(nums[1]), val(nums[2], int), val(nums[3])])
    if len(obs) < 10:
        raise ValueError("station table could not be read")
    return {"date": odate, "stations": obs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignore the minimum interval")
    ap.add_argument("--min-hours", type=float, default=float(os.environ.get("MIN_HOURS", 36)))
    ap.add_argument("--midday"), ap.add_argument("--temprain"), ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    prev = None
    if os.path.exists(a.out):
        with open(a.out) as fh:
            prev = json.load(fh)
    if prev and not a.force and not (a.midday or a.temprain):
        last = dt.datetime.fromisoformat(prev["fetched_at"].replace("Z", "+00:00"))
        age = (now - last).total_seconds() / 3600
        if age < a.min_hours - 0.5:
            print(f"Last refresh {age:.1f} h ago; next one after {a.min_hours} h. Nothing to do.")
            return 0

    mid = open(a.midday, "rb").read() if a.midday else fetch("midday.pdf")
    tr = open(a.temprain, "rb").read() if a.temprain else fetch("temprain.pdf")
    try:
        bulletin = parse_midday(mid)
    except ValueError as e:
        print(f"::warning::Midday bulletin not parsed ({e}). Keep previous weather.json.")
        return 0
    try:
        obs = parse_temprain(tr)
    except Exception as e:  # observations are optional; keep the forecast
        print("Warning: observations skipped:", e, file=sys.stderr)
        obs = prev.get("obs") if prev else {"date": None, "stations": {}}

    out = {
        "source": "India Meteorological Department, Met Centre Bhubaneswar",
        "source_url": "https://mausam.imd.gov.in/bhubaneswar/",
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bulletin": bulletin,
        "obs": obs,
    }
    with open(a.out, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"Wrote {a.out}: bulletin {bulletin['date']} {bulletin['time']}, {len(bulletin['days'])} days, "
          f"{len(obs['stations'])} districts with observations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
