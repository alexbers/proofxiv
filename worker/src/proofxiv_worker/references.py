"""Bibliography check: every reference of a paper looked up in arXiv, Crossref and zbMATH, no model.

A reference that names an identifier (arXiv id, DOI) must resolve to a work with the same
title; one without is searched by its text. Books, theses and web pages are often missing
from these indexes, so not finding them leaves them 'unchecked' rather than 'not_found'."""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from proofxiv_protocol.runs import ReferenceItem, ReferenceReport

from . import __version__

# (url, query parameters) -> (HTTP status, body); status 0 means the network failed
Fetch = Callable[[str, dict[str, str]], tuple[int, bytes]]

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF = "https://api.crossref.org/works"
ZBMATH = "https://api.zbmath.org/v1/document/_search"
DATACITE = "https://api.datacite.org/dois"
HANDLES = "https://doi.org/api/handles"
# seconds between two requests to one host: arXiv asks for one request every three seconds
INTERVALS = {"export.arxiv.org": 3.0, "api.zbmath.org": 1.0, "api.crossref.org": 0.2, "api.datacite.org": 0.2, "doi.org": 0.2}
RETRY_STATUS = {0, 429, 500, 502, 503, 504}
MAX_REFERENCES = 300
ARXIV_BATCH = 40
MATCH, CLOSE = 0.85, 0.6  # title similarity: the same work / the same work, retitled
# kinds of works these indexes rarely cover: not finding them proves nothing
UNINDEXED_TYPES = {
    "book", "booklet", "phdthesis", "mastersthesis", "thesis", "techreport", "report",
    "manual", "misc", "online", "electronic", "www", "unpublished",
}
UNINDEXED_WORDS = (
    "thesis", "dissertation", "manuscript", "preprint", "in preparation", "personal communication",
    "private communication", "unpublished", "lecture notes", "http", "www.", "press", "springer",
    "verlag", "publishers", "edition", "book",
)
ATOM = "{http://www.w3.org/2005/Atom}"

_ENTRY_HEAD = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
_FIELD = re.compile(r"[\s,]*([A-Za-z][\w\-:.]*)\s*=\s*")
_BARE = re.compile(r"[^,#\s{}]+")
_BIBITEM = re.compile(r"\\bibitem\s*(?:\[[^\]]{0,300}\])?\s*\{([^{}]{1,200})\}")
_END_BIBLIOGRAPHY = re.compile(r"\\end\s*\{thebibliography\}")
_BBL_ENTRY = re.compile(r"\\entry\s*\{([^{}]{1,200})\}\s*\{([A-Za-z]{1,30})\}")
_BBL_FIELD = re.compile(r"\\field\s*\{([a-z]{1,30})\}\s*\{")
_BBL_VERB = re.compile(r"\\verb\s*\{([a-z]{1,30})\}\s*\\verb\s+([^\n]{1,500}?)\s*\\endverb")
_BBL_FAMILY = re.compile(r"family=\{([^{}]{1,100})\}")
_CITE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?\s*(?:\[[^\]]{0,200}\]\s*){0,2}\{([^{}]{1,2000})\}")
_TEX_COMMENT = re.compile(r"(?<!\\)%[^\n]*")
_EMPH = re.compile(r"\\(?:emph|textit|textsl)\s*\{|\{\\(?:em|it|sl)\s")
_QUOTED = re.compile(r"``(.{5,300}?)''")
_ARXIV_REF = re.compile(
    r"arxiv(?:\.org/(?:abs|pdf)/|[^0-9a-z]{0,3}(?:preprint\s+)?(?:arxiv\s*:\s*)?)"
    r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)
_ARXIV_ID = re.compile(r"^\s*(?:arxiv:\s*)?(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?\s*$", re.I)
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"{}<>\\]+")
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_ACCENT = re.compile(r"\\[\'\"`^~=.]|\\[uvHckrbd](?![A-Za-z])\s*")  # \"o, \H o, \v{s}
_OLD_ARXIV_CLASS = re.compile(r"^([a-z\-]+)\.[a-z]{2}/", re.IGNORECASE)  # math.CO/0601001
_COMMAND = re.compile(r"\\([A-Za-z]+)\*?\s*")
# commands that are words of the title ($\pi$), not formatting
_GREEK = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "varpi", "rho", "varrho", "sigma", "tau",
    "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
}
_SYMBOL = re.compile(r"\\(.)")
_TAG = re.compile(r"<[^<>]{0,80}>")
_NON_WORD = re.compile(r"[\W_]+")
_SUBTITLE = re.compile(r"[:?!.]\s|\s[-–—]{1,3}\s")
# letters that are commands in LaTeX and do not decompose in Unicode
_LETTERS = {"\\l": "ł", "\\L": "Ł", "\\o": "ø", "\\O": "Ø", "\\ss": "ß", "\\ae": "æ", "\\oe": "œ", "\\i": "ı"}
_LETTER = re.compile(r"\\(?:ss|ae|oe|[lLoOi])(?![A-Za-z])\s*")
_FOLD = str.maketrans(
    {"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i", "đ": "d"}
    # π in a title found online, \pi in the paper's .bib
    | {c: f" {unicodedata.name(c).split()[-1].lower()} " for c in "αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩ"}
)


@dataclass
class Entry:
    key: str
    kind: str  # BibTeX entry type, or "bibitem" for free text
    text: str
    title: str = ""
    year: str = ""
    doi: str = ""
    arxiv: str = ""
    path: str = ""
    surname: str = ""  # of the first author


@dataclass
class Found:
    title: str
    source: str
    url: str


class Unavailable(RuntimeError):
    """No lookup service answered: the job goes back instead of reporting nothing as found."""


# --- text ------------------------------------------------------------------------------------


def plain(text: str) -> str:
    """LaTeX to readable text: accents and commands dropped, their arguments kept."""
    text = _ACCENT.sub("", text.replace("\\newblock", " "))
    text = _LETTER.sub(lambda m: _LETTERS[m.group(0).rstrip()], text)
    text = _COMMAND.sub(lambda m: f" {m.group(1)} " if m.group(1) in _GREEK else "", text.replace("~", " "))
    text = _SYMBOL.sub(r"\1", text)
    return " ".join(text.replace("{", "").replace("}", "").replace("$", "").split())


def norm(text: str) -> str:
    """For comparing titles: plain, without HTML tags, accents, case or punctuation."""
    text = plain(_TAG.sub(" ", text)).translate(_FOLD)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(_NON_WORD.sub(" ", text.lower()).split())


def similarity(a: str, b: str) -> float:
    a_n, b_n = norm(a)[:300], norm(b)[:300]
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    # a subtitle added or dropped ("Small Ramsey numbers" / "...: a dynamic survey"), but not
    # any longer title that starts the same ("...K-theory" / "...K-theory over algebras")
    a_main, b_main = norm(_SUBTITLE.split(a, maxsplit=1)[0]), norm(_SUBTITLE.split(b, maxsplit=1)[0])
    if len(min(a_n, b_n, key=len)) >= 20 and (a_main == b_n or b_main == a_n):
        return 1.0
    return SequenceMatcher(None, a_n, b_n, autojunk=False).ratio()


def coverage(title: str, text: str) -> float:
    """How much of a found title appears in a reference given as free text."""
    title_n, text_n = norm(title), norm(text)
    if len(title_n) >= 12 and title_n in text_n:
        return 1.0
    words = {w for w in title_n.split() if len(w) >= 3}
    if len(words) < 2:
        return 0.0
    return len(words & set(text_n.split())) / len(words)


def _find_doi(text: str) -> str:
    m = _DOI.search(text)
    return m.group(0).rstrip(".,;:)]").lower() if m else ""


def _arxiv_id(raw: str) -> str:
    """The id as arXiv's API names it: old-style ids lose their subject class."""
    return _OLD_ARXIV_CLASS.sub(r"\1/", raw).lower() if "/" in raw else raw


def _find_arxiv(text: str) -> str:
    m = _ARXIV_REF.search(text)
    return _arxiv_id(m.group(1)) if m else ""


# --- parsing ---------------------------------------------------------------------------------


def _balanced(s: str, start: int) -> tuple[str, int]:
    """The text up to the brace closing the one just before `start`, and the index after it."""
    depth = 1
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start:i], i + 1
    return s[start:], len(s)


def _entry_body(s: str, start: int, close: str) -> tuple[str, int]:
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0 and close == "}":
                return s[start:i], i + 1
            depth -= 1
        elif c == ")" and depth == 0 and close == ")":
            return s[start:i], i + 1
    return s[start:], len(s)


def _read_value(s: str, i: int) -> tuple[str, int]:
    """A field value: {...}, "..." or a bare word, possibly joined with #."""
    parts: list[str] = []
    while i < len(s):
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s):
            break
        if s[i] == "{":
            value, i = _balanced(s, i + 1)
        elif s[i] == '"':
            j, depth = i + 1, 0
            while j < len(s) and not (s[j] == '"' and depth == 0):
                depth += {"{": 1, "}": -1}.get(s[j], 0)
                j += 1
            value, i = s[i + 1 : j], j + 1
        else:
            m = _BARE.match(s, i)
            if m is None:
                break
            value, i = m.group(0), m.end()
        parts.append(value)
        while i < len(s) and s[i].isspace():
            i += 1
        if i < len(s) and s[i] == "#":
            i += 1
            continue
        break
    return "".join(parts), i


def _bib_fields(s: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    i = 0
    while i < len(s):
        m = _FIELD.match(s, i)
        if m is None:  # not a field: skip to the next comma
            comma = s.find(",", i + 1)
            if comma < 0:
                break
            i = comma
            continue
        value, i = _read_value(s, m.end())
        fields.setdefault(m.group(1).lower(), value)
    return fields


def _arxiv_of(fields: dict[str, str]) -> str:
    prefix = (fields.get("archiveprefix") or fields.get("eprinttype") or "").lower()
    for name in ("eprint", "arxiv", "arxivid"):
        m = _ARXIV_ID.match(fields.get(name, ""))
        if m and (name != "eprint" or prefix in ("", "arxiv")):
            return _arxiv_id(m.group(1))
    blob = " ".join(fields.get(k, "") for k in ("url", "note", "journal", "journaltitle", "howpublished"))
    return _find_arxiv(blob)


def _entry(key: str, kind: str, fields: dict[str, str], path: str) -> Entry:
    title = plain(fields.get("title", ""))
    authors = plain(fields.get("author") or fields.get("editor") or "")
    venue = plain(
        next(
            (fields[k] for k in ("journal", "journaltitle", "booktitle", "publisher", "howpublished", "school")
             if fields.get(k)),
            "",
        )
    )
    year = (_YEAR.search(fields.get("year") or fields.get("date") or "") or [""])[0]
    doi = _find_doi(fields.get("doi", "")) or _find_doi(" ".join(fields.get(k, "") for k in ("url", "note")))
    arxiv = _arxiv_of(fields)
    if doi.startswith("10.48550/arxiv."):  # arXiv's own DOIs
        arxiv, doi = arxiv or _arxiv_id(doi.removeprefix("10.48550/arxiv.")), ""
    text = ". ".join(p for p in (authors, title, venue, year) if p)
    first = authors.split(" and ")[0]
    surname = first.split(",")[0] if "," in first else (first.split() or [""])[-1]
    return Entry(key.strip(), kind, text[:600], title, year, doi, arxiv, path, surname.strip())


def parse_bib(text: str, path: str = "") -> list[Entry]:
    entries: list[Entry] = []
    i = 0
    while (at := text.find("@", i)) >= 0:
        m = _ENTRY_HEAD.match(text, at)
        if m is None:
            i = at + 1
            continue
        body, i = _entry_body(text, m.end(), "}" if m.group(2) == "{" else ")")
        kind = m.group(1).lower()
        if kind in ("comment", "preamble", "string"):
            continue
        key, _, rest = body.partition(",")
        entries.append(_entry(key, kind, _bib_fields(rest), path))
    return entries


def _title_of(raw: str) -> str:
    """The title inside a free-text reference, when its style marks it."""
    blocks = raw.split("\\newblock")
    if len(blocks) >= 3:  # BibTeX styles: authors \newblock title \newblock venue
        title = plain(blocks[1]).strip(" .,")
        if len(title.split()) >= 2:
            return title
    m = _EMPH.search(raw)
    if m:
        title = plain(_balanced(raw, m.end())[0]).strip(" .,")
        if len(title.split()) >= 2:
            return title
    m = _QUOTED.search(raw)
    return plain(m.group(1)).strip(" .,") if m else ""


def parse_bibitems(text: str, path: str = "") -> list[Entry]:
    """\\bibitem entries of a thebibliography environment (.tex, or a .bbl written by BibTeX)."""
    text = _TEX_COMMENT.sub("", text)
    items = list(_BIBITEM.finditer(text))
    entries = []
    for m, following in zip(items, [*items[1:], None], strict=False):  # the last one has no next
        end = following.start() if following else len(text)
        stop = _END_BIBLIOGRAPHY.search(text, m.end(), end)
        raw = text[m.end() : stop.start() if stop else end][:5000]
        flat = plain(raw)
        years = _YEAR.findall(flat)
        entries.append(
            Entry(
                key=m.group(1).strip(),
                kind="bibitem",
                text=flat[:600],
                title=_title_of(raw),
                year=years[-1] if years else "",
                doi=_find_doi(raw),
                arxiv=_find_arxiv(raw),
                path=path,
            )
        )
    return entries


def parse_biblatex(text: str, path: str = "") -> list[Entry]:
    """Entries of a .bbl written by biber (biblatex)."""
    entries = []
    for m in _BBL_ENTRY.finditer(text):
        end = text.find("\\endentry", m.end())
        body = text[m.end() : end if end >= 0 else m.end() + 20000]
        fields: dict[str, str] = {}
        for f in _BBL_FIELD.finditer(body):
            fields.setdefault(f.group(1), _balanced(body, f.end())[0])
        for v in _BBL_VERB.finditer(body):
            fields.setdefault(v.group(1), v.group(2).strip())
        family = _BBL_FAMILY.search(body)
        if family:
            fields["author"] = family.group(1)
        entries.append(_entry(m.group(1), m.group(2).lower(), fields, path))
    return entries


def cited_keys(text: str) -> tuple[set[str], bool]:
    """Keys cited in a .tex file, and whether it cites everything (\\nocite{*})."""
    keys: set[str] = set()
    for m in _CITE.finditer(_TEX_COMMENT.sub("", text)):
        keys |= {k.strip().lower() for k in m.group(1).split(",") if k.strip()}
    return keys - {"*"}, "*" in keys


def collect(files: dict[str, str]) -> list[Entry]:
    """The paper's bibliography from its .bib, .bbl and .tex files; .bib entries the .tex
    files never cite are left out, and each key counts once (.bib first: it says most)."""
    order = {".bib": 0, ".bbl": 1, ".tex": 2}
    entries: list[Entry] = []
    cited: set[str] = set()
    cite_all = False
    for path in sorted(files, key=lambda p: (order.get(PurePosixPath(p).suffix.lower(), 3), p)):
        text, suffix = files[path], PurePosixPath(path).suffix.lower()
        if suffix == ".bib":
            entries += parse_bib(text, path)
            continue
        entries += parse_biblatex(text, path) + parse_bibitems(text, path)
        if suffix == ".tex":
            keys, everything = cited_keys(text)
            cited |= keys
            cite_all = cite_all or everything
    if cited and not cite_all:
        entries = [e for e in entries if not e.path.endswith(".bib") or e.key.lower() in cited]
    seen: set[str] = set()
    unique = []
    for entry in entries:
        if entry.key.lower() not in seen:
            seen.add(entry.key.lower())
            unique.append(entry)
    return unique


# --- lookups ---------------------------------------------------------------------------------


def _atom_entries(body: bytes) -> list[tuple[str, str]] | None:
    """(arXiv id without version, title) of each entry of an arXiv API answer."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    out = []
    for entry in root.iter(f"{ATOM}entry"):
        ident = (entry.findtext(f"{ATOM}id") or "").strip()
        if "/abs/" not in ident:  # error entries point to api/errors
            continue
        arxiv = re.sub(r"v\d+$", "", ident.split("/abs/", 1)[1])
        out.append((arxiv, " ".join((entry.findtext(f"{ATOM}title") or "").split())))
    return out


def _unindexed(entry: Entry) -> bool:
    if entry.kind in UNINDEXED_TYPES:
        return True
    return entry.kind == "bibitem" and any(w in entry.text.lower() for w in UNINDEXED_WORDS)


def _score(entry: Entry, title: str) -> float:
    if not title:
        return 0.0
    return similarity(entry.title, title) if entry.title else coverage(title, entry.text)


def _item(entry: Entry, status: str, found: Found | None = None, note: str = "") -> ReferenceItem:
    return ReferenceItem(
        key=entry.key[:200],
        text=entry.text[:600],
        status=status,
        found_title=found.title[:600] if found and found.title else None,
        source=found.source if found else None,
        url=found.url if found else None,
        note=note,
    )


class Resolver:
    def __init__(
        self,
        fetch: Fetch,
        contact: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        intervals: dict[str, float] | None = None,
        deadline: float | None = None,
    ) -> None:
        self.fetch = fetch
        self.contact = contact
        self.sleep = sleep
        self.clock = clock
        self.intervals = INTERVALS if intervals is None else intervals
        self.deadline = deadline
        self.last: dict[str, float] = {}
        self.answered = 0
        self.failed = 0

    def _get(self, url: str, params: dict[str, str] | None = None) -> tuple[int, bytes] | None:
        """A polite GET (one host at a time, retried on overload); None when it never answered."""
        host = urlparse(url).hostname or ""
        for attempt in range(3):
            wait = self.last.get(host, -math.inf) + self.intervals.get(host, 0.0) - self.clock()
            if wait > 0:
                self.sleep(wait)
            self.last[host] = self.clock()
            status, body = self.fetch(url, params or {})
            if status not in RETRY_STATUS:
                self.answered += 1
                return status, body
            if attempt < 2:
                self.sleep(5.0 * (attempt + 1))
        self.failed += 1
        return None

    def _json(self, url: str, params: dict[str, str] | None = None) -> tuple[int, Any] | None:
        got = self._get(url, params)
        if got is None:
            return None
        status, body = got
        if status != 200:
            return status, None
        try:
            return status, json.loads(body)
        except ValueError:
            return None

    def _polite(self, params: dict[str, str]) -> dict[str, str]:
        return {**params, "mailto": self.contact} if self.contact else params

    def arxiv(self, ids: list[str]) -> dict[str, Found | None]:
        """id -> the paper, or None when arXiv has no such paper; ids it never answered for
        are left out."""
        out: dict[str, Found | None] = {}
        batches = [ids[i : i + ARXIV_BATCH] for i in range(0, len(ids), ARXIV_BATCH)]
        while batches:
            batch = batches.pop(0)
            got = self._get(ARXIV_API, {"id_list": ",".join(batch), "max_results": str(len(batch))})
            entries = _atom_entries(got[1]) if got and got[0] == 200 else None
            if entries is None:
                if got is not None and len(batch) > 1:  # one malformed id spoils a batch
                    batches = [[i] for i in batch] + batches
                continue
            titles = dict(entries)
            for arxiv in batch:
                title = titles.get(re.sub(r"v\d+$", "", arxiv))
                out[arxiv] = Found(title, "arxiv", f"https://arxiv.org/abs/{arxiv}") if title else None
        return out

    def doi(self, doi: str) -> tuple[str, Found | None]:
        """("found", work) | ("absent", None) | ("failed", None), via Crossref, then DataCite,
        then the DOI registry itself (the title is unknown there)."""
        url, path = f"https://doi.org/{doi}", quote(doi, safe="/")
        crossref = self._json(f"{CROSSREF}/{path}", self._polite({}))
        if crossref is None:
            return "failed", None
        if crossref[0] == 200 and crossref[1] is not None:
            titles = (crossref[1].get("message") or {}).get("title") or [""]
            return "found", Found(" ".join(str(titles[0]).split()), "crossref", url)
        datacite = self._json(f"{DATACITE}/{path}")
        if datacite is None:
            return "failed", None
        if datacite[0] == 200 and datacite[1] is not None:
            titles = ((datacite[1].get("data") or {}).get("attributes") or {}).get("titles") or [{}]
            return "found", Found(str(titles[0].get("title", "")), "datacite", url)
        registry = self._json(f"{HANDLES}/{path}")
        if registry is None:
            return "failed", None
        if registry[0] == 200 and registry[1] is not None and registry[1].get("responseCode") == 1:
            return "found", Found("", "doi.org", url)
        return "absent", None

    def search(self, entry: Entry) -> tuple[str, Found | None]:
        """("found", work) | ("absent", None) | ("failed", None): Crossref's reference
        matching on the text, then zbMATH (mathematics back to the 1860s) and arXiv by title."""
        answered = False
        crossref = self._json(
            CROSSREF,
            self._polite({"query.bibliographic": entry.text[:300], "rows": "5", "select": "DOI,title"}),
        )
        if crossref is not None:
            answered = True
            if crossref[1] is not None:
                for work in (crossref[1].get("message") or {}).get("items") or []:
                    title = " ".join(str((work.get("title") or [""])[0]).split())
                    if _score(entry, title) >= MATCH:
                        return "found", Found(title, "crossref", f"https://doi.org/{work.get('DOI', '')}")
        words = norm(entry.title).split()[:12]
        if words:
            query = f'ti:"{" ".join(words)}"' + (f" & au:{norm(entry.surname)}" if norm(entry.surname) else "")
            zbmath = self._json(ZBMATH, {"search_string": query, "results_per_page": "5"})
            if zbmath is not None:
                answered = True  # 404: nothing found
                for work in (zbmath[1] or {}).get("result") or []:
                    title = " ".join(str((work.get("title") or {}).get("title") or "").split())
                    if _score(entry, title) >= MATCH:
                        return "found", Found(title, "zbmath", str(work.get("zbmath_url") or ""))
        if len(words) >= 3:
            got = self._get(ARXIV_API, {"search_query": f'ti:"{" ".join(words)}"', "max_results": "5"})
            entries = _atom_entries(got[1]) if got and got[0] == 200 else None
            if entries is not None:
                answered = True
                for arxiv, title in entries:
                    if _score(entry, title) >= MATCH:
                        return "found", Found(title, "arxiv", f"https://arxiv.org/abs/{arxiv}")
        return ("absent" if answered else "failed"), None

    def _judge(self, entry: Entry, found: Found, what: str) -> ReferenceItem:
        if not found.title:
            return _item(entry, "verified", found, f"the {what} exists; its title was not compared")
        if not entry.title and not entry.text:
            return _item(entry, "verified", found, f"the {what} exists")
        score = _score(entry, found.title)
        if score >= MATCH:
            return _item(entry, "verified", found)
        if score >= CLOSE:
            return _item(entry, "verified", found, "the title differs slightly from the record")
        return _item(entry, "mismatch", found, f"the {what} belongs to a work with a different title")

    def one(self, entry: Entry, arxiv: dict[str, Found | None]) -> ReferenceItem:
        if entry.arxiv:
            if entry.arxiv not in arxiv:
                return _item(entry, "unchecked", note="arXiv did not answer")
            found = arxiv[entry.arxiv]
            if found is None:
                return _item(entry, "not_found", note=f"arXiv has no paper {entry.arxiv}")
            return self._judge(entry, found, f"arXiv id {entry.arxiv}")
        if entry.doi:
            state, found = self.doi(entry.doi)
            if state == "failed":
                return _item(entry, "unchecked", note="the DOI services did not answer")
            if found is None:
                return _item(entry, "not_found", note=f"DOI {entry.doi} is not registered")
            return self._judge(entry, found, f"DOI {entry.doi}")
        if len(norm(entry.text)) < 10:
            return _item(entry, "unchecked", note="too little text to look up")
        state, found = self.search(entry)
        if state == "failed":
            return _item(entry, "unchecked", note="Crossref, zbMATH and arXiv did not answer")
        if found is not None:
            return _item(entry, "verified", found, "found by search")
        if _unindexed(entry):
            return _item(
                entry, "unchecked", note="not in Crossref, zbMATH or arXiv; books, theses and web pages often are not"
            )
        return _item(entry, "not_found", note="no matching work in Crossref, zbMATH or arXiv")

    def resolve(self, entries: list[Entry]) -> list[ReferenceItem]:
        checked = entries[:MAX_REFERENCES]
        arxiv = self.arxiv(sorted({e.arxiv for e in checked if e.arxiv}))
        items = []
        for entry in checked:
            if self.deadline is not None and self.clock() > self.deadline:
                items.append(_item(entry, "unchecked", note="the job's time limit was reached"))
            else:
                items.append(self.one(entry, arxiv))
        items += [
            _item(e, "unchecked", note=f"only the first {MAX_REFERENCES} references are checked")
            for e in entries[MAX_REFERENCES:]
        ]
        if self.failed and not self.answered:
            raise Unavailable(f"{self.failed} lookups failed and none succeeded")
        return items


def http_fetch(contact: str | None = None, timeout: float = 30.0) -> Fetch:
    import httpx

    agent = f"proofxiv-worker/{__version__}" + (f" (mailto:{contact})" if contact else "")
    client = httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": agent})

    def fetch(url: str, params: dict[str, str]) -> tuple[int, bytes]:
        try:
            resp = client.get(url, params=params)
        except httpx.HTTPError as exc:
            return 0, str(exc).encode()
        return resp.status_code, resp.content

    return fetch


def check(files: dict[str, str], resolver: Resolver) -> ReferenceReport:
    return ReferenceReport(items=resolver.resolve(collect(files)))
