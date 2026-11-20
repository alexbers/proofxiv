"""Bibliography check: parsing .bib/.bbl/.tex and resolving against arXiv and Crossref (faked)."""

from __future__ import annotations

import json

import pytest
from proofxiv_worker import references
from proofxiv_worker.references import Resolver, collect, parse_bib, parse_bibitems, parse_biblatex

BIB = r"""
@string{ejc = "Electron. J. Combin."}
@article{radz,
  author = {Radziszowski, Stanis{\l}aw},
  title = {Small {R}amsey Numbers},
  journal = ejc,
  year = 2021,
  doi = {https://doi.org/10.37236/21},
}
@misc{fake,
  title = "A proof of " # "everything",
  author = "Nobody, A.",
  eprint = {2301.99999}, archivePrefix = {arXiv},
}
@article{wrong, title = {On the chromatic number of the plane}, author = {X, Y},
  note = {arXiv:2301.00001}}
@book{bollobas, title = {Random Graphs}, author = {Bollob{\'a}s, B{\'e}la},
  publisher = {Cambridge University Press}, year = {2001}}
@article{uncited, title = {Never cited in the paper}, journal = {J. Nowhere}, year = {1999}}
@article{lost, title = {Hypergraph colourings beyond the probabilistic method},
  author = {Doe, J.}, journal = {Ann. of Math.}, year = {2019}}
@article{eigen, author = {Eigen, S. J.}, title = {Putting convergent sequences into measurable sets},
  journal = {Studia Sci. Math. Hungar.}, year = {1985}}
@article(old, title = {Lower bounds for small diagonal Ramsey numbers (part {II})},
  eprint = {math.CO/0601001})
"""

TEX = r"""\documentclass{article}
\begin{document}
See \cite{radz,fake} and \citep[p.~3]{wrong}, \citet{bollobas}, \cite{lost, old, eigen}, \cite{erdos}.
% \cite{uncited} is commented out
\begin{thebibliography}{9}
\bibitem[Erd{\H o}s(1947)]{erdos} P.~Erd{\H o}s.
\newblock Some remarks on the theory of graphs.
\newblock {\em Bull. Amer. Math. Soc.}, 53:292--294, 1947.
\end{thebibliography}
\end{document}
"""


def atom(entries: list[tuple[str, str]]) -> bytes:
    body = "".join(
        f"<entry><id>http://arxiv.org/abs/{i}v1</id><title>{t}</title></entry>" for i, t in entries
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


class FakeWeb:
    """arXiv, Crossref, DataCite and the DOI registry as dictionaries."""

    def __init__(self) -> None:
        self.arxiv = {
            "2301.00001": "Graph colourings and quantum games",
            "math/0601001": "Lower bounds for small diagonal Ramsey numbers (part II)",
        }
        self.dois = {"10.37236/21": "Small Ramsey Numbers"}
        self.search_hits = [
            ("10.1090/s0002-9904-1947-08785-1", "Some remarks on the theory of graphs"),
            ("10.1000/other", "Colourings of hypergraphs with few edges"),
        ]
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.down = False

    def __call__(self, url: str, params: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, params))
        if self.down:
            return 0, b"connection refused"
        if url == references.ARXIV_API:
            if "id_list" in params:
                ids = params["id_list"].split(",")
                return 200, atom([(i, self.arxiv[i]) for i in ids if i in self.arxiv])
            return 200, atom([])  # title search finds nothing
        if url.startswith(references.CROSSREF + "/"):
            doi = url.removeprefix(references.CROSSREF + "/")
            if doi in self.dois:
                return 200, json.dumps({"message": {"title": [self.dois[doi]]}}).encode()
            return 404, b"Resource not found."
        if url == references.ZBMATH:
            if "au:eigen" in params["search_string"]:
                work = {"title": {"title": "Putting convergent sequences into measurable sets"},
                        "zbmath_url": "https://zbmath.org/4019379"}
                return 200, json.dumps({"result": [work]}).encode()
            return 404, b'{"result": null}'
        if url == references.CROSSREF:
            items = [{"DOI": d, "title": [t]} for d, t in self.search_hits]
            return 200, json.dumps({"message": {"items": items}}).encode()
        return 404, b"{}"


def resolver(web: FakeWeb, **kw) -> Resolver:
    return Resolver(web, "ops@example.org", sleep=lambda s: None, intervals={}, **kw)


def test_parse_bib_fields_and_identifiers():
    entries = {e.key: e for e in parse_bib(BIB, "refs.bib")}
    assert set(entries) == {"radz", "fake", "wrong", "bollobas", "uncited", "lost", "old", "eigen"}
    assert entries["eigen"].surname == "Eigen" and entries["radz"].surname == "Radziszowski"
    assert entries["radz"].title == "Small Ramsey Numbers" and entries["radz"].doi == "10.37236/21"
    assert entries["radz"].text.startswith("Radziszowski, Stanisław. Small Ramsey Numbers. ejc. 2021")
    assert entries["fake"].title == "A proof of everything" and entries["fake"].arxiv == "2301.99999"
    assert entries["wrong"].arxiv == "2301.00001"
    assert entries["old"].arxiv == "math/0601001" and entries["old"].kind == "article"
    assert entries["bollobas"].text.startswith("Bollobas, Bela. Random Graphs. Cambridge")


def test_bibitems_and_biblatex():
    (erdos,) = parse_bibitems(TEX, "paper.tex")
    assert erdos.key == "erdos" and erdos.title == "Some remarks on the theory of graphs"
    assert erdos.year == "1947" and erdos.text.startswith("P. Erdos.")
    amsplain = r"\bibitem{k} A.~Author, \emph{A title in emphasis}, J. Math. \textbf{1} (2000), arXiv:2101.00002."
    (item,) = parse_bibitems(amsplain)
    assert item.title == "A title in emphasis" and item.arxiv == "2101.00002"
    bbl = r"""
\entry{smith20}{article}{}
  \name{author}{1}{}{{{hash=1}{family={Smith},given={Ann}}}}
  \field{title}{Colourings of {$K_5$}}
  \field{year}{2020}
  \verb{doi}
  \verb 10.1000/xyz.1
  \endverb
\endentry
"""
    (smith,) = parse_biblatex(bbl, "paper.bbl")
    assert (smith.key, smith.title, smith.year, smith.doi) == ("smith20", "Colourings of K_5", "2020", "10.1000/xyz.1")


def test_collect_keeps_cited_entries_once():
    entries = collect({"paper.tex": TEX, "refs.bib": BIB, "paper.bbl": TEX})
    keys = [e.key for e in entries]
    assert "uncited" not in keys and keys.count("erdos") == 1
    assert set(keys) == {"radz", "fake", "wrong", "bollobas", "lost", "old", "eigen", "erdos"}
    nocite = TEX.replace(r"\cite{erdos}", r"\cite{erdos}\nocite{*}")
    assert "uncited" in [e.key for e in collect({"paper.tex": nocite, "refs.bib": BIB})]


def test_resolve_statuses():
    web = FakeWeb()
    report = references.check({"paper.tex": TEX, "refs.bib": BIB}, resolver(web))
    status = {i.key: i.status for i in report.items}
    assert status == {
        "radz": "verified",  # DOI resolves to the same title
        "fake": "not_found",  # arXiv has no such paper
        "wrong": "mismatch",  # the arXiv id belongs to another paper
        "old": "verified",  # old-style id, subject class dropped
        "bollobas": "unchecked",  # a book: absence from the indexes proves nothing
        "lost": "not_found",  # an article nobody can find
        "erdos": "verified",  # found by Crossref's reference matching
        "eigen": "verified",  # an old article: found in zbMATH by title and first author
    }
    items = {i.key: i for i in report.items}
    assert items["wrong"].found_title == "Graph colourings and quantum games"
    assert items["erdos"].url == "https://doi.org/10.1090/s0002-9904-1947-08785-1"
    assert items["radz"].source == "crossref" and items["old"].source == "arxiv"
    assert items["eigen"].source == "zbmath" and items["eigen"].url == "https://zbmath.org/4019379"
    # all arXiv ids go in one request; Crossref gets the contact address
    assert sum(1 for url, p in web.calls if "id_list" in p) == 1
    assert all(p.get("mailto") == "ops@example.org" for url, p in web.calls if "crossref" in url)


def test_mismatched_doi_and_registry_fallback():
    web = FakeWeb()
    web.dois["10.1000/a"] = "An entirely different subject"
    bib = "@article{a, title={Ramsey numbers of cycles}, doi={10.1000/a}}\n@article{b, title={Ramsey numbers}, doi={10.1000/nope}}"
    items = {i.key: i for i in references.check({"r.bib": bib}, resolver(web)).items}
    assert items["a"].status == "mismatch" and "DOI 10.1000/a" in items["a"].note
    assert items["b"].status == "not_found" and "not registered" in items["b"].note
    tried = [url for url, _ in web.calls if "10.1000/nope" in url]
    assert [u.split("/")[2] for u in tried] == ["api.crossref.org", "api.datacite.org", "doi.org"]


def test_network_down_gives_the_job_back():
    web = FakeWeb()
    web.down = True
    with pytest.raises(references.Unavailable):
        references.check({"refs.bib": BIB}, resolver(web))


def test_polite_intervals():
    web = FakeWeb()
    waits: list[float] = []
    r = Resolver(web, sleep=waits.append, clock=lambda: 100.0)
    r.arxiv(["2301.00001"])
    r.arxiv(["math/0601001"])
    assert waits == [references.INTERVALS["export.arxiv.org"]]


def test_limit_and_deadline():
    bib = "\n".join(f"@book{{b{i}, title={{Book number {i} on graphs}}}}" for i in range(references.MAX_REFERENCES + 2))
    clock = iter(range(10**6))
    web = FakeWeb()
    items = Resolver(web, sleep=lambda s: None, intervals={}, clock=lambda: next(clock), deadline=50).resolve(
        references.collect({"r.bib": bib})
    )
    assert len(items) == references.MAX_REFERENCES + 2
    assert items[-1].note.startswith("only the first") and items[-3].note.startswith("the job's time limit")


def test_title_similarity():
    sim = references.similarity
    assert sim("Small Ramsey numbers", "Small Ramsey Numbers: a dynamic survey") == 1.0  # subtitle
    assert sim(r"On the approximation of $\pi$", "On the approximation of π") == 1.0
    assert sim(r"Erd{\H o}s--Ko--Rado for {\l}ukasz", "Erdős–Ko–Rado for Łukasz") == 1.0
    # a longer title that only starts the same is another work
    assert sim("Vector Bundles and K-Theory", "Vector bundles and K-theory over topological algebras") < references.MATCH
    assert sim("Graph colourings", "Quantum games") < references.CLOSE
