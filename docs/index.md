---
title: "GEMEA: German Memory Atlas"
---
# GEMEA: German Memory Atlas

**GeMeA** (German Memory Atlas) is an open knowledge graph over approximately 27 million digitised objects from the [German Digital Library](https://www.deutsche-digitale-bibliothek.de/) (DDB). The corpus spans seven cultural heritage sectors — archives, libraries, museums, media libraries, research institutions, monument preservation offices, and others — and is aligned to the [mocho](https://github.com/ISE-FIZKarlsruhe/mocho) mid-level ontology for interoperability.

The graph is indexed in [QLever](https://github.com/ad-freiburg/qlever) for high-performance SPARQL querying and published through [SHMARQL](https://github.com/epoz/shmarql) as a dereferenceable Linked Data browser. AI agents can query it directly via the [mcp-server-qlever](https://github.com/xorwell/mcp-server-qlever) using the Model Context Protocol.

## Statistics

| Metric | Value |
| ------ | ----- |
| Digitised objects | 27M+ |
| Triples | 3.1B |
| Named graphs | 4 |
| Cultural heritage sectors | 7 |

## Explore

- **[Explore (SHMARQL)](https://gemea.ise.fiz-karlsruhe.de/shmarql/)** — the dereferenceable Linked Data browser for the GeMeA knowledge graph.
- **[Download data](https://gemea.ise.fiz-karlsruhe.de/downloads)** — RDF datasets and artefacts from the GeMeA knowledge graph.
- **[GitHub](https://github.com/ise-fizkarlsruhe/gemea)** — source code of the GeMeA knowledge graph.

## Citation

```bibtex
@misc{tan2026swib,
  author       = {Tan, Mary Ann},
  year         = {2026},
  month        = nov,
  title        = {{GEMEA: Germany's Cultural Memory as a Linked Knowledge Graph}},
  howpublished = {{Presentation at SWIB26: Semantic Web in Libraries, online, 18 November 2026}},
  url          = {https://gemea.ise.fiz-karlsruhe.de/}
}
``` 

## Learn more

- [Corpus](/corpus/)
- [Architecture](/architecture/)
- [Paper](/paper/)
- [Data Stories](/data-stories/)
- [GitHub](https://github.com/ise-fizkarlsruhe/gemea)
- [SHMARQL](https://gemea.ise.fiz-karlsruhe.de/shmarql/)
