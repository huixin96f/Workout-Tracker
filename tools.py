#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
workout-history.html  ---  validator + publish build, in one file.

No node on this machine; JS is exercised through JavaScriptCore (osascript -l
JavaScript), everything else is stdlib python3.

  python3 tools.py validate   data + runtime checks on the SOURCE file
  python3 tools.py fonts      fetch ASCII-subset webfonts into .build/fonts
  python3 tools.py build      validate, then emit .build/workout-history.artifact.html
  python3 tools.py preview    build, then serve a host-mimicking page at :8765
  python3 tools.py all        validate + build

Nothing is written unless every check passes; every failure exits non-zero.
The generated file is disposable -- never hand-edit it, edit workout-history.html.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "workout-history.html")
BUILD_DIR = os.path.join(HERE, ".build")
OUT = os.path.join(BUILD_DIR, "workout-history.artifact.html")
PREVIEW = os.path.join(BUILD_DIR, "preview.html")
MANIFEST = os.path.join(BUILD_DIR, "publish.json")
FONT_DIR = os.path.join(BUILD_DIR, "fonts")

ROOT = "#wt-root"          # wrapper the whole artifact is re-rooted onto
BG = "#10130f"             # --bg, duplicated onto html/body so overscroll is dark

KNOWN_UNITS = {
    "each", "each/侧", "each·步", "双手合拿",
    "双手合拿/侧", "配重", "配重/侧",
    "加片", "自重", "自重/侧", "秒",
}


class Fail(Exception):
    pass


def die(msg):
    raise Fail(msg)


def read_src():
    with open(SRC, encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------- regions ---

def regions(src):
    """Split the source into (title, css, markup, js). Fails if shape changed."""
    m_style = re.search(r"<style>(.*?)</style>", src, re.S)
    m_script = re.search(r"<script>(.*?)</script>", src, re.S)
    m_title = re.search(r"<title>(.*?)</title>", src, re.S)
    if not m_style:
        die("no <style> block found in source")
    if not m_script:
        die("no <script> block found in source")
    if not m_title:
        die("no <title> found in source")
    if src.count("<style>") != 1 or src.count("<script>") != 1:
        die("expected exactly one <style> and one <script> block")
    markup = src[m_style.end():m_script.start()]
    if "<meta" in markup or "<title" in markup:
        die("unexpected <meta>/<title> in the markup region")
    return m_title.group(1), m_style.group(1), markup, m_script.group(1)


# --------------------------------------------------------------- validator ---

def extract_sessions(js):
    """Pull the SESSIONS literal out of the script and parse it as JSON."""
    i = js.find("const SESSIONS = [")
    if i < 0:
        die("SESSIONS array not found (expected `const SESSIONS = [`)")
    start = js.index("[", i)
    depth, j = 0, start
    while j < len(js):
        c = js[j]
        if c == '"':                       # skip strings
            j += 1
            while j < len(js) and js[j] != '"':
                j += 2 if js[j] == "\\" else 1
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    if depth != 0:
        die("SESSIONS array is not bracket-balanced")
    raw = js[start:j + 1]
    txt = re.sub(r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', raw)
    txt = re.sub(r",(\s*[}\]])", r"\1", txt)
    try:
        return json.loads(txt), raw
    except Exception as e:
        die("SESSIONS does not parse as data: %s" % e)


def check_sessions(sessions, js, markup):
    errs, warns = [], []
    if not sessions:
        errs.append("SESSIONS is empty")
        return errs, warns

    seen_dates = set()
    prev = None
    for k, s in enumerate(sessions):
        tag = "session[%d]" % k
        d = s.get("date")
        if not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or ""):
            errs.append("%s: bad or missing date %r" % (tag, d))
            continue
        tag = "session %s" % d
        if d in seen_dates:
            errs.append("%s: duplicate date" % tag)
        seen_dates.add(d)
        if prev and d <= prev:
            errs.append("%s: dates must be strictly ascending (after %s)" % (tag, prev))
        prev = d
        if s.get("day") not in (1, 2, 3, 4):
            errs.append("%s: day must be 1-4, got %r" % (tag, s.get("day")))
        if "partial" in s and s["partial"] is not True:
            errs.append("%s: partial, if present, must be true" % tag)
        exs = s.get("exercises")
        if not isinstance(exs, list) or not exs:
            errs.append("%s: missing exercises" % tag)
            continue
        names = set()
        for ex in exs:
            n = ex.get("name")
            if not isinstance(n, str) or not n:
                errs.append("%s: exercise with missing name" % tag)
                continue
            if n in names:
                errs.append("%s: duplicate exercise %s" % (tag, n))
            names.add(n)
            u = ex.get("unit")
            if not isinstance(u, str) or not u:
                errs.append("%s / %s: missing unit" % (tag, n))
            elif u not in KNOWN_UNITS:
                warns.append("%s / %s: unfamiliar unit %r" % (tag, n, u))
            sets = ex.get("sets")
            if not isinstance(sets, list):
                errs.append("%s / %s: sets must be a list" % (tag, n))
                continue
            if ex.get("skipped") and sets:
                errs.append("%s / %s: skipped but sets is not empty" % (tag, n))
            if not sets and not ex.get("skipped"):
                errs.append("%s / %s: empty sets without skipped:true" % (tag, n))
            for st in sets:
                if (not isinstance(st, list) or len(st) != 2
                        or not all(isinstance(v, (int, float)) for v in st)):
                    errs.append("%s / %s: bad set %r (want [weight, reps])" % (tag, n, st))
                    continue
                w, r = st
                if w < 0:
                    errs.append("%s / %s: negative weight %r" % (tag, n, w))
                if r <= 0:
                    errs.append("%s / %s: non-positive reps/seconds %r" % (tag, n, r))

    # every day used must exist in DAY_META
    meta_days = set(int(x) for x in re.findall(r"^\s*(\d+):\s*\{", js, re.M))
    for s in sessions:
        if s.get("day") in (1, 2, 3, 4) and s["day"] not in meta_days:
            errs.append("day %s used but missing from DAY_META" % s["day"])

    # bodyweight/time registry must agree with the data
    m = re.search(r"BODYWEIGHT_OR_TIME\s*=\s*new Set\(\[(.*?)\]\)", js, re.S)
    if not m:
        errs.append("BODYWEIGHT_OR_TIME set not found")
        bw = set()
    else:
        bw = set(re.findall(r'"([^"]+)"', m.group(1)))
    allzero, anyweight = set(), set()
    for s in sessions:
        for ex in s["exercises"]:
            sets = ex.get("sets") or []
            if not sets:
                continue
            (allzero if all(w == 0 for w, _ in sets) else anyweight).add(ex["name"])
    for n in sorted(allzero - anyweight):
        if n not in bw:
            errs.append("%s has no weights but is not in BODYWEIGHT_OR_TIME "
                        "(its trend chart would be blank)" % n)
    for n in sorted(bw & anyweight):
        errs.append("%s is in BODYWEIGHT_OR_TIME but has real weights" % n)

    # calendar only renders a hardcoded year + month list
    m = re.search(r"const months=\[([\d,\s]*)\]", js)
    if not m:
        errs.append("calendar months array not found")
    else:
        months = set(int(x) for x in re.findall(r"\d+", m.group(1)))
        years = set(int(re.search(r"new Date\((\d{4})", js).group(1))
                    for _ in [0]) if re.search(r"new Date\((\d{4})", js) else set()
        for s in sessions:
            y, mo = int(s["date"][:4]), int(s["date"][5:7])
            if mo not in months:
                errs.append("%s falls in month %d, which the calendar does not "
                            "render (const months=[...])" % (s["date"], mo))
            if years and y not in years:
                errs.append("%s is in year %d, but the calendar is hardcoded to %s"
                            % (s["date"], y, sorted(years)))

    # footer date range + subtitle start date must track the data
    first, last = sessions[0]["date"], sessions[-1]["date"]
    m = re.search(r"数据区间\s*(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", js)
    if not m:
        errs.append("footer date range not found")
    else:
        if m.group(1) != first:
            errs.append("footer range starts %s but first session is %s" % (m.group(1), first))
        if m.group(2) != last:
            errs.append("footer range ends %s but last session is %s" % (m.group(2), last))
    m = re.search(r'class="sub">自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', markup)
    if not m:
        errs.append("subtitle start date not found")
    else:
        sub = "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
        if sub != first:
            errs.append("subtitle says %s but first session is %s" % (sub, first))

    return errs, warns


JS_HARNESS = r"""
// ---- minimal DOM stub: enough to render the artifact headlessly ----
var LOG = [];
var REG = [];
function parseTags(html){
  var out=[], re=/<([A-Za-z]+)\s+([^>]*?)\/?>/g, m;
  while((m=re.exec(html))){
    var attrs={}, are=/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g, a;
    while((a=are.exec(m[2]))) attrs[a[1]] = (a[2]!==undefined?a[2]:a[3]);
    out.push(attrs);
  }
  return out;
}
function pseudo(attrs){
  return {
    className: attrs["class"]||"",
    dataset: { date: attrs["data-date"], pt: attrs["data-pt"] },
    getAttribute: function(k){ return attrs[k]; },
    setAttribute: function(){},
    addEventListener: function(){},
    style: {},
    classList: { add:function(){}, remove:function(){}, contains:function(c){
      return (attrs["class"]||"").split(/\s+/).indexOf(c)>=0; } }
  };
}
function El(tag){
  this.tagName=tag; this._html=""; this.children=[]; this.style={};
  this.dataset={}; this.className=""; this.textContent=""; this._attrs={};
  var self=this;
  this.classList={ add:function(c){ self.className=(self.className+" "+c).trim(); },
                   remove:function(c){ self.className=self.className.split(/\s+/)
                       .filter(function(x){return x!==c;}).join(" "); },
                   contains:function(c){ return self.className.split(/\s+/).indexOf(c)>=0; } };
  REG.push(this);
}
Object.defineProperty(El.prototype,"innerHTML",{
  get:function(){ return this._html; },
  set:function(v){ this._html=String(v); this.children=[]; }
});
El.prototype.appendChild=function(c){ this.children.push(c); return c; };
El.prototype.insertBefore=function(c){ this.children.unshift(c); return c; };
El.prototype.addEventListener=function(){};
El.prototype.setAttribute=function(k,v){ this._attrs[k]=v; };
El.prototype.getAttribute=function(k){ return this._attrs[k]; };
El.prototype.getBoundingClientRect=function(){ return {width:220,height:140,top:0,left:0}; };
El.prototype._collect=function(acc){
  acc.push(this._html);
  this.children.forEach(function(c){ c._collect(acc); });
  return acc;
};
El.prototype.querySelectorAll=function(sel){
  var want=sel.split(".").filter(function(s){return s.length;});
  var res=[];
  (function walk(n){
    n.children.forEach(function(c){
      var cl=(c.className||"").split(/\s+/);
      if(want.every(function(w){ return cl.indexOf(w)>=0; })) res.push(c);
      walk(c);
    });
  })(this);
  this._collect([]).forEach(function(h){
    parseTags(h).forEach(function(a){
      var cl=(a["class"]||"").split(/\s+/);
      if(want.every(function(w){ return cl.indexOf(w)>=0; })) res.push(pseudo(a));
    });
  });
  return res;
};
var BYID={};
["statStrip","tip","dayPick","dayHeading","chartsGrid","projCaption",
 "tableDayPick","tablePanel","calHolder","calLegend","footer"].forEach(function(id){
  BYID[id]=new El("div");
});
var document={
  getElementById:function(id){
    if(!BYID[id]) throw new Error("script asked for missing element #"+id);
    return BYID[id];
  },
  createElement:function(t){ return new El(t); },
  addEventListener:function(){},
  querySelectorAll:function(sel){
    var want=sel.split(".").filter(function(s){return s.length;});
    var res=[];
    REG.forEach(function(n){
      n._collect([]).forEach(function(h){
        parseTags(h).forEach(function(a){
          var cl=(a["class"]||"").split(/\s+/);
          if(want.every(function(w){ return cl.indexOf(w)>=0; })) res.push(pseudo(a));
        });
      });
    });
    return res;
  }
};
var window={innerWidth:390,innerHeight:844};
// ---- artifact script ----
%(SCRIPT)s
// ---- driver: exercise every view, every day, every tooltip ----
var result={ok:true,errors:[],stats:{}};
try{
  // each day is drawn separately, so check each day's points while it is on screen
  var totalPts=0;
  [1,2,3,4].forEach(function(day){
    drawDayCharts(day);
    var pts=BYID.chartsGrid.querySelectorAll(".pt-hit");
    if(!pts.length) result.errors.push("day "+day+" rendered no chart data points");
    totalPts+=pts.length;
    pts.forEach(function(el){
      var raw=el.getAttribute("data-pt"), d;
      try{ d=JSON.parse(raw); }catch(e){ result.errors.push("bad data-pt JSON on day "+day+": "+raw); return; }
      if(!d.name||!d.date) result.errors.push("data-pt missing name/date: "+raw);
      showPointTip({clientX:10,clientY:10}, d);
      if(!BYID.tip.innerHTML) result.errors.push("empty tooltip for "+d.name);
    });
    if(!BYID.dayHeading.innerHTML) result.errors.push("day "+day+" heading did not render");
  });
  result.stats.points=totalPts;
  ["all","1","2","3","4"].forEach(function(f){
    renderTable(f);
    if(!BYID.tablePanel.innerHTML) result.errors.push("table filter "+f+" rendered nothing");
  });
  renderCalendar();

  var cells=BYID.calHolder.querySelectorAll(".cal-cell.has");
  result.stats.calCells=cells.length;
  SESSIONS.forEach(function(s){
    var hit=cells.filter(function(c){ return c.dataset.date===s.date; });
    if(hit.length!==1) result.errors.push("calendar cell missing for "+s.date);
    showTip({clientX:10,clientY:10}, s);
  });

  result.stats.sessions=SESSIONS.length;
  result.stats.statStrip=(BYID.statStrip.innerHTML.match(/class="stat"/g)||[]).length;
  if(result.stats.statStrip!==4) result.errors.push("stat strip should have 4 items, got "+result.stats.statStrip);
  if(!BYID.footer.innerHTML) result.errors.push("footer did not render");
  if(!BYID.tablePanel.innerHTML) result.errors.push("table did not render");
  if(!BYID.projCaption.innerHTML) result.errors.push("projection caption did not render");
}catch(e){
  result.ok=false;
  result.errors.push("runtime error: "+(e && e.message ? e.message : String(e)));
}
if(result.errors.length) result.ok=false;
var __RESULT__ = JSON.stringify(result);
"""

# How each engine is invoked, and how it hands __RESULT__ back on stdout.
# node prints nothing on its own; osascript echoes the script's last expression.
JS_ENGINES = [
    ("node", ["node"], "console.log(__RESULT__);"),
    ("osascript", ["osascript", "-l", "JavaScript"], "__RESULT__;"),
]


def js_engine():
    """First available JS engine, so this is not macOS-only.

    Returns (label, argv, tail) or (None, None, None) when the machine has
    neither node nor JavaScriptCore.
    """
    import shutil
    for label, argv, tail in JS_ENGINES:
        if shutil.which(argv[0]):
            return label, argv, tail
    return None, None, None


def run_js(js):
    """Run the artifact's script against a DOM stub.

    Returns None (with a loud warning) when no JS engine exists -- the data
    checks still run, but the render check cannot, and the caller says so
    rather than pretending the file was fully verified.
    """
    label, argv, tail = js_engine()
    if not label:
        print("  WARN: no JS engine found (need `node` or macOS `osascript`).")
        print("  WARN: skipping the runtime render check -- data checks only.")
        return None
    harness = JS_HARNESS % {"SCRIPT": js} + "\n" + tail + "\n"
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(harness)
        try:
            p = subprocess.run(argv + [path], capture_output=True, text=True)
        except OSError as e:
            die("could not start the JS engine %s: %s" % (label, e))
        if p.returncode != 0:
            die("JS did not execute (%s): %s"
                % (label, p.stderr.strip() or p.stdout.strip()))
        out = p.stdout.strip()
        try:
            return json.loads(out)
        except Exception:
            die("JS harness produced no usable result (%s):\n%s\n%s"
                % (label, out, p.stderr.strip()))
    finally:
        os.unlink(path)


def validate(verbose=True):
    src = read_src()
    title, css, markup, js = regions(src)

    for name, txt in (("script", js), ("style", css)):
        if txt.count("{") != txt.count("}"):
            die("%s block has unbalanced braces" % name)

    sessions, _ = extract_sessions(js)
    errs, warns = check_sessions(sessions, js, markup)

    res = run_js(js)
    if res is not None:
        errs.extend(res.get("errors", []))

    if warns and verbose:
        for w in warns:
            print("  warn: %s" % w)
    if errs:
        die("validation failed:\n" + "\n".join("  - %s" % e for e in errs))
    if verbose:
        if res is None:
            print("validate OK (data only, NO render check): %d sessions, %s -> %s"
                  % (len(sessions), sessions[0]["date"], sessions[-1]["date"]))
        else:
            st = res.get("stats", {})
            print("validate OK: %d sessions, %d chart points, %d calendar cells, "
                  "%s -> %s" % (st.get("sessions", 0), st.get("points", 0),
                                st.get("calCells", 0), sessions[0]["date"],
                                sessions[-1]["date"]))
    return src, title, css, markup, js, sessions


# ------------------------------------------------------------------- fonts ---

FONT_QUERIES = [
    # opsz pinned near the sizes actually used (heads 30-46px, stat digits 27px):
    # leaving it variable doubles the payload for no visible gain.
    ("fraunces", "Fraunces:ital,opsz,SOFT,WONK,wght@0,36,0,0,500;0,36,0,0,600;1,36,0,0,500"),
    ("archivo", "Archivo:wght@400;500;600;700"),
    ("archivo-narrow", "Archivo+Narrow:wght@500;600"),
    ("spline-mono", "Spline+Sans+Mono:wght@400;500"),
]
SUBSET = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
          "abcdefghijklmnopqrstuvwxyz .,:;/()[]%+-=<>~*&#@!?'\"|_")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch_fonts():
    """Cache ASCII-subset woff2 faces so the published page keeps its type.

    The artifact host blocks external requests, so the Google Fonts @import in
    the source never loads there. Latin text and digits are the only thing these
    faces cover anyway (CJK always falls back), so an ASCII subset is enough.
    """
    os.makedirs(FONT_DIR, exist_ok=True)
    import base64
    import urllib.parse
    total, seen = 0, {}
    for slug, fam in FONT_QUERIES:
        url = ("https://fonts.googleapis.com/css2?family=%s&text=%s&display=swap"
               % (fam, urllib.parse.quote(SUBSET, safe="")))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        css = urllib.request.urlopen(req, timeout=25).read().decode("utf-8")
        faces = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
        if not faces:
            die("no @font-face returned for %s" % fam)
        out = []
        for body in faces:
            # subsetted faces come back as /l/font?kit=... with no .woff2 suffix,
            # and a variable family points every weight at the same file
            m = re.search(r"url\((https://[^)]+)\)\s*format\('woff2'\)", body)
            if not m:
                continue
            url2 = m.group(1)
            if url2 not in seen:
                data = urllib.request.urlopen(
                    urllib.request.Request(url2, headers={"User-Agent": UA}),
                    timeout=25).read()
                seen[url2] = base64.b64encode(data).decode("ascii")
                total += len(data)
            out.append(re.sub(r"src:\s*url\([^)]+\)[^;]*;",
                              "src:url(data:font/woff2;base64,%s) format('woff2');" % seen[url2],
                              body, count=1).strip())
        with open(os.path.join(FONT_DIR, slug + ".css"), "w", encoding="ascii") as f:
            f.write("\n".join("@font-face{%s}" % b for b in out))
        print("  fonts: %-15s %d faces" % (slug, len(out)))
    print("fonts cached in .build/fonts (%.0f KB of woff2)" % (total / 1024.0))


def inline_fonts():
    if not os.path.isdir(FONT_DIR):
        return ""
    css = []
    for slug, _ in FONT_QUERIES:
        p = os.path.join(FONT_DIR, slug + ".css")
        if os.path.exists(p):
            with open(p, encoding="ascii") as f:
                css.append(f.read())
    return "\n".join(css)


# ------------------------------------------------------------------- build ---

TRANSLIT = {
    "—": "--", "–": "-", "→": "->", "·": "-",
    "×": "x", "²": "2", "≥": ">=", "≤": "<=",
    "“": '"', "”": '"', "‘": "'", "’": "'",
}


def esc_js(s):
    out = []
    for ch in s:
        cp = ord(ch)
        if cp < 128:
            out.append(ch)
        elif cp <= 0xFFFF:
            out.append("\\u%04x" % cp)
        else:
            cp -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)))
    return "".join(out)


def esc_markup(s):
    return "".join(ch if ord(ch) < 128 else "&#%d;" % ord(ch) for ch in s)


def ascii_css(css):
    """CSS carries no entity decoding, so make it ASCII by hand.

    Non-ASCII is only ever allowed inside comments (transliterated); anywhere
    else it would be a real value and silently corrupting it is worse than
    failing the build.
    """
    spans = [m.span() for m in re.finditer(r"/\*.*?\*/", css, re.S)]

    def in_comment(i):
        return any(a <= i < b for a, b in spans)

    bad = [(i, ch) for i, ch in enumerate(css) if ord(ch) > 127 and not in_comment(i)]
    if bad:
        die("non-ASCII in CSS outside a comment (would need a real \\XXXX escape): "
            + ", ".join("%r@%d" % (ch, i) for i, ch in bad[:6]))
    out = []
    for i, ch in enumerate(css):
        if ord(ch) < 128:
            out.append(ch)
        else:
            out.append(TRANSLIT.get(ch, "?"))
            if ch not in TRANSLIT:
                print("  warn: CSS comment character %r transliterated to '?'" % ch)
    return "".join(out)


def scope_selectors(prelude, state):
    comments = re.findall(r"/\*.*?\*/", prelude, re.S)
    sel = re.sub(r"/\*.*?\*/", "", prelude, flags=re.S).strip()
    parts = [p.strip() for p in sel.split(",") if p.strip()]
    new = []
    for p in parts:
        if p == ":root":
            new.append(":root")
        elif p == "body":
            state["body"] += 1
            new.append(ROOT)
        elif p == "*":
            state["star"] += 1
            new.extend([ROOT, ROOT + " *"])
        elif p.startswith(ROOT) or p.split()[0] in ("html", "@page"):
            new.append(p)
        else:
            new.append(ROOT + " " + p)
    lead = ("\n" + "\n".join(comments) + "\n") if comments else "\n"
    return lead + ", ".join(new)


def transform_css(css, state):
    """Re-root every rule onto the wrapper; nothing may target the host page."""
    out, buf, i, n = "", "", 0, len(css)
    while i < n:
        if css.startswith("/*", i):
            j = css.find("*/", i + 2)
            j = n if j < 0 else j + 2
            buf += css[i:j]
            i = j
            continue
        c = css[i]
        if c == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                if css.startswith("/*", j):
                    k = css.find("*/", j + 2)
                    j = n if k < 0 else k + 2
                    continue
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                j += 1
            body, prelude, buf = css[i + 1:j - 1], buf, ""
            head = prelude.strip()
            if re.match(r"/\*.*?\*/\s*$", head, re.S):
                head = ""
            stripped = re.sub(r"/\*.*?\*/", "", prelude, flags=re.S).strip()
            if stripped.startswith("@media") or stripped.startswith("@supports"):
                out += prelude + "{" + transform_css(body, state) + "}"
            elif stripped.startswith("@"):
                out += prelude + "{" + body + "}"
            else:
                extra = " min-height:100vh;" if stripped == "body" else ""
                out += scope_selectors(prelude, state) + " {" + body + extra + "}"
            i = j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and css[j] != c:
                j += 2 if css[j] == "\\" else 1
            if j >= n:
                die("unterminated %s string in CSS at offset %d" % (c, i))
            buf += css[i:j + 1]
            i = j + 1
            continue
        if c == "(":
            depth, j = 1, i + 1
            while j < n and depth:
                if css[j] == "(":
                    depth += 1
                elif css[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                die("unterminated ( in CSS at offset %d" % i)
            buf += css[i:j]
            i = j
            continue
        # a statement at-rule ends at the first `;` outside quotes and parens --
        # @import's url() is full of semicolons, and splitting inside it produced
        # a mangled selector before this was handled
        if c == ";" and re.sub(r"/\*.*?\*/", "", buf, flags=re.S).strip().startswith("@"):
            out += buf + ";"
            buf = ""
            i += 1
            continue
        buf += c
        i += 1
    return out + buf


BOOTSTRAP = """
<script>
/* The host wraps this file in its own document skeleton, so the source's
   viewport meta never survives. Without it the page lays out at desktop
   width on a phone, which defeats the point of publishing it. */
(function(){
  try{
    var h=document.head||document.getElementsByTagName('head')[0];
    if(h&&!h.querySelector('meta[name="viewport"]')){
      var m=document.createElement('meta');
      m.setAttribute('name','viewport');
      m.setAttribute('content','width=device-width, initial-scale=1');
      h.appendChild(m);
    }
  }catch(e){}
})();
</script>
"""


def build():
    src, title, css, markup, js, sessions = validate()

    state = {"body": 0, "star": 0}
    css_ascii = ascii_css(css)

    # The host blocks every external request, so the Google Fonts @import can
    # never load there; we ship the same faces inline instead. Leaving a dead
    # @import behind would also be invalid CSS once @font-face precedes it.
    fonts = inline_fonts()
    # the url() itself contains semicolons (weight lists), so match it whole
    import_re = r"@import\s+url\([^()]*\)[^;]*;"
    imports = re.findall(import_re, css_ascii)
    if fonts:
        if not imports:
            die("expected a font @import in the source to replace with inline faces")
        css_ascii = re.sub(import_re, "", css_ascii)
        if "@import" in css_ascii:
            die("an @import survived the strip -- refusing to emit half-removed CSS")
    elif imports:
        print("  warn: no cached fonts (run `python3 tools.py fonts`); the published "
              "page will fall back to system faces")

    css_out = transform_css(css_ascii, state)
    if state["body"] != 1:
        die("expected exactly one `body` rule to re-root, found %d" % state["body"])
    if state["star"] != 1:
        die("expected the universal `*` reset to re-root, found %d" % state["star"])
    if re.search(r"(^|\n|\})\s*(body|html|\*)\s*[,{]", css_out):
        die("a rule still targets the host document after re-rooting")
    for prelude in re.findall(r"(?:^|[}\n])([^{}@;]*?)\{", css_out):
        if re.search(r"[&<>?]", prelude):
            die("mangled selector produced by the CSS rewrite: %r" % prelude.strip()[:90])
    if "@import" in css_out:
        die("@import survived into the publishable file")

    css_out = (fonts + "\n" + css_out if fonts else css_out) + \
        "\nhtml, body { background: %s; }\n" % BG

    # No <title> in the output: it would have to be entity-escaped like the rest
    # of the markup, and the publisher may lift it verbatim into the tab and the
    # gallery card. The title travels as a publish parameter instead -- see
    # .build/publish.json, which pins the identity across republishes.
    parts = [
        "<style>%s</style>" % css_out,
        BOOTSTRAP.strip(),
        '<div id="wt-root">%s</div>' % esc_markup(markup),
        "<script>%s</script>" % esc_js(js),
    ]
    out = "\n".join(parts) + "\n"

    # tag-boundary match, so <header>/<thead> are not mistaken for the real thing
    leak = re.search(r"<\s*/?\s*(html|head|body)\s*(>|\s)|<!doctype|<\s*meta\b",
                     out, re.I)
    if leak:
        die("document-level tag leaked into the publishable file: %r at offset %d"
            % (leak.group(0), leak.start()))
    if not out.isascii():
        die("output is not pure ASCII: %r" % [c for c in out if ord(c) > 127][:6])
    if "wt-root" not in css_out:
        die("stylesheet was not re-rooted")

    os.makedirs(BUILD_DIR, exist_ok=True)
    with open(OUT, "w", encoding="ascii") as f:
        f.write(out)

    # Publish identity, kept in one place so every republish reuses it verbatim:
    # the URL is bookmarked, and a changed path or favicon strands the bookmark.
    manifest = {
        # relative on purpose: an absolute path breaks the moment this folder is
        # copied or moved, and publishing the wrong path mints a NEW url and
        # strands the bookmark. Resolve it against the folder holding tools.py.
        "file_path": os.path.relpath(OUT, HERE),
        "title": title,
        "favicon": "\U0001f3cb️",
        "description": ("四日哑铃训练循环的完整历史：趋势图、数据表与日历，"
                        "含线性回归预测。"),
        "note": "Republish this exact file_path to keep the URL. Pass the saved "
                "url when publishing from a new conversation.",
    }
    prev = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            prev = json.load(f)
    if prev.get("url"):
        manifest["url"] = prev["url"]
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("build OK: %s (%.1f KB, ASCII, %d sessions)"
          % (os.path.relpath(OUT, HERE), len(out) / 1024.0, len(sessions)))
    print("publish as: title=%s favicon=%s%s"
          % (manifest["title"], manifest["favicon"],
             " url=" + manifest["url"] if manifest.get("url") else
             " (no url recorded yet -- save it into .build/publish.json)"))
    return out


# ----------------------------------------------------------------- preview ---

def preview(port=8765):
    """Serve the generated file inside a deliberately hostile skeleton.

    No charset meta and no viewport meta in the head -- the worst case the host
    can hand us -- so anything that only works because of the source's own
    <head> shows up here.
    """
    out = build()
    with open(PREVIEW, "w", encoding="ascii") as f:
        f.write("<!doctype html>\n<html>\n<head>\n<title>preview</title>\n"
                "</head>\n<body>\n" + out + "\n</body>\n</html>\n")
    import http.server
    import socketserver

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=BUILD_DIR, **kw)

        def guess_type(self, path):
            return "text/html" if path.endswith(".html") else super().guess_type(path)

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), H) as httpd:
        print("preview at http://127.0.0.1:%d/preview.html (ctrl-c to stop)" % port)
        httpd.serve_forever()


def streaks(threshold=3):
    """Rule 11.5: never suggest adding weight off a hunch.

    Counts, per exercise, how many training sessions in a row used an
    identical set scheme (every set's weight and reps, and the set count).
    Only a run longer than `threshold` earns a mention.
    """
    src = read_src()
    _, _, _, js = regions(src)
    sessions, _ = extract_sessions(js)

    hist = {}
    for s in sessions:
        for ex in s["exercises"]:
            if ex.get("skipped") or not ex.get("sets"):
                continue
            key = tuple(tuple(x) for x in ex["sets"])
            hist.setdefault(ex["name"], []).append((s["date"], s["day"], key))

    rows = []
    for name, apps in hist.items():
        run, last = 1, apps[-1][2]
        for date, day, key in reversed(apps[:-1]):
            if key == last:
                run += 1
            else:
                break
        since = apps[-run][0]
        rows.append((run, name, apps[-1][2], since, apps[-1][0], len(apps)))
    rows.sort(reverse=True)

    flagged = [r for r in rows if r[0] > threshold]
    print("connective runs of identical weight x reps (threshold >%d):" % threshold)
    for run, name, key, since, last, total in rows:
        mark = "  << 提醒加重" if run > threshold else ""
        scheme = ", ".join("%gx%g" % (w, r) for w, r in key)
        print("  %-14s %d次相同  [%s]  %s..%s (共%d次记录)%s"
              % (name, run, scheme, since, last, total, mark))
    print("\n%d exercise(s) over threshold" % len(flagged))
    return flagged


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    try:
        if cmd == "validate":
            validate()
        elif cmd == "streaks":
            streaks(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
        elif cmd == "fonts":
            fetch_fonts()
        elif cmd == "build":
            build()
        elif cmd == "preview":
            preview(int(sys.argv[2]) if len(sys.argv) > 2 else 8765)
        elif cmd == "all":
            build()
        else:
            print(__doc__)
            return 2
    except Fail as e:
        print("FAIL: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
