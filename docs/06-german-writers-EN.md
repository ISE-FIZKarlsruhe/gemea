# 📖 Data Story: How to Become Immortal

*A true story, measured in 3.15 billion triples, told by a knowledge graph that doesn't know who's famous — and gives it away anyway.*

**Author:** Harald Sack, revised by Mary Ann Tan

**Source:** GEMEA Knowledge Graph (German Digital Library / Deutsche Digitale Bibliothek), ~3.15 bn triples · ~27.5 m objects
**Subject:** German writers of the 19th and 20th centuries
**As of:** 17 November 2026

**How the figures were found:** Harald Sack first explored the story by asking Claude questions in natural language through the [mcp-server-qlever](https://github.com/xorwell/mcp-server-qlever) MCP server connected to the GEMEA SPARQL endpoint (setup: [GEMEA README §5.1](https://github.com/ISE-FIZKarlsruhe/gemea#51-llm-client), Option A). The key figures were then rewritten as the SPARQL queries embedded below, which run live against `https://gemea.ise.fiz-karlsruhe.de/sparql` each time this story is opened.

```shmarql
# shmarql-label: Objects in GEMEA

PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT (xsd:integer(COUNT(?object)) AS ?objects)
WHERE {
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?object a edm:ProvidedCHO .
  }
}
```

## Prologue: The Promise of the Stage

Anyone who wanted to become immortal in the 19th century had to get on stage. The literary histories don't say this — the numbers do.

Goethe leads the field with 4,393 objects, closely followed by Schiller (3,787). But the real hero of the data is an unremarkable scrap of paper: the theatre playbill (*Theaterzettel*). More than half of Schiller's works (2,141 of 3,787) are playbills — for *Wilhelm Tell* (410 objects) and the *Wallenstein* trilogy (468), the performance announcements flutter through the graph by the hundreds. Goethe's *Faust* (677, scattered across two dozen spellings) towers above all. For each of the three plays, 74–83 % of the objects are playbills.

```shmarql
# shmarql-label: The big plays: objects and playbills

PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?play
       (xsd:integer(COUNT(DISTINCT ?work)) AS ?objects)
       (xsd:integer(COUNT(DISTINCT ?playbill)) AS ?playbills)
WHERE {
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    {
      ?work dc:creator <http://d-nb.info/gnd/118540238> ; dc:title ?title .
      FILTER(CONTAINS(LCASE(STR(?title)), "faust"))
      BIND("Faust (Goethe)" AS ?play)
    } UNION {
      ?work dc:creator <http://d-nb.info/gnd/118607626> ; dc:title ?title .
      FILTER(CONTAINS(LCASE(STR(?title)), "wilhelm tell"))
      BIND("Wilhelm Tell (Schiller)" AS ?play)
    } UNION {
      ?work dc:creator <http://d-nb.info/gnd/118607626> ; dc:title ?title .
      FILTER(CONTAINS(LCASE(STR(?title)), "wallenstein") || CONTAINS(LCASE(STR(?title)), "piccolomini"))
      BIND("Wallenstein trilogy (Schiller)" AS ?play)
    } UNION {
      ?work dc:creator <http://d-nb.info/gnd/118607626> ; dc:title ?title .
      FILTER(CONTAINS(LCASE(STR(?title)), "gedichte"))
      BIND("Gedichte (Schiller)" AS ?play)
    }
    OPTIONAL { ?work dc:type "Theaterzettel" . BIND(?work AS ?playbill) }
  }
}
GROUP BY ?play
ORDER BY DESC(?objects)
```

```shmarql
# shmarql-label: Works, playbills, and objects about each writer

PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?writer
       (xsd:integer(COUNT(DISTINCT ?work)) AS ?works_as_creator)
       (xsd:integer(COUNT(DISTINCT ?playbill)) AS ?playbills)
       (xsd:integer(COUNT(DISTINCT ?about)) AS ?objects_about)
WHERE {
  VALUES (?gnd ?writer) {
    (<http://d-nb.info/gnd/118540238> "Johann Wolfgang von Goethe")
    (<http://d-nb.info/gnd/118607626> "Friedrich Schiller")
    (<http://d-nb.info/gnd/118548018> "Heinrich Heine")
    (<http://d-nb.info/gnd/118516906> "Georg Büchner")
    (<http://d-nb.info/gnd/118546937> "Gerhart Hauptmann")
    (<http://d-nb.info/gnd/118577166> "Thomas Mann")
    (<http://d-nb.info/gnd/118577131> "Heinrich Mann")
    (<http://d-nb.info/gnd/118509047> "Gottfried Benn")
  }
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    { ?work dc:creator ?gnd }
    UNION { ?playbill dc:creator ?gnd ; dc:type "Theaterzettel" }
    UNION { ?about dcterms:subject ?gnd }
  }
}
GROUP BY ?writer
ORDER BY DESC(?works_as_creator)
```

The punchline: whoever wrote drama, won. Schiller's *Gedichte* (poems) have a playbill share of 0 % — you can't put lyric poetry on a poster. Quiet verse, loud stages.

Heinrich Heine proves it from the other side. With 541 works he is a giant in print, yet only 31 of them are playbills (6 %). The greatest lyric poet of the 19th century survived on the page, not on the poster.

Two institutions divide this hoard like two dragons guarding their gold: the Bavarian State Library (the printed Goethe) and the Thuringian Main State Archive in Weimar (the performed Goethe). Together they hold ~96 % of all *Faust* objects.

```shmarql
# shmarql-view: barchart
# shmarql-x: institution
# shmarql-y: faust_objects
# shmarql-label: Where Goethe's Faust is held

PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?institution (xsd:integer(COUNT(DISTINCT ?work)) AS ?faust_objects)
WHERE {
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?work dc:creator <http://d-nb.info/gnd/118540238> ;
          dc:title ?title .
    FILTER(CONTAINS(LCASE(STR(?title)), "faust"))
    ?agg edm:aggregatedCHO ?work ;
         edm:dataProvider ?institution .
    FILTER(isLiteral(?institution))
  }
}
GROUP BY ?institution
ORDER BY DESC(?faust_objects)
LIMIT 5
```

## Act I: The Copyright Cliff

Then, around 1900, the floor gives way.

Heinrich Mann, a major novelist of the 20th century: 52 works as creator. Gottfried Benn, a century-defining poet: exactly 2 works of his own — the novellas *Diesterweg* and *Etappe*. His famous poems? Nowhere to be found. Not because they're unimportant — but because they stay under copyright until the last day of 2026.

This is where the whole narrative flips. The classics are creators (Goethe: 4,393 works by him). The moderns are subjects: Heinrich Mann appears in 347 objects as a topic, but barely as an author. Benn comes to 50 objects: 2 by him, 48 about him.

You could put it this way: In the 19th century you wrote books; in the 20th you got photographed.

## Act II: The Photographic Afterlife

And photographed they were. With the camera, fame acquires a face.

Among the Literature Nobel laureates, one name explodes: Gerhart Hauptmann — 842 images, 802 of them from a single collection. George Bernard Shaw (523), Thomas Mann (350 images). Where Goethe was still etched in copper, Hauptmann — and above all his plays — were shot on film by the dozen: 479 of those 802 are scene photos of *Die Weber*, *Die Ratten*, *Florian Geyer* and the rest. Even in the photographic age, the stage wins. The Deutsche Fotothek becomes the secret protagonist of this story — it supplies the faces of modernity, just as Weimar supplied the playbills of the classical age.
```shmarql
# shmarql-view: barchart
# shmarql-x: writer
# shmarql-y: images
# shmarql-label: Images linked to Nobel laureates in literature

PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?writer (xsd:integer(COUNT(DISTINCT ?image)) AS ?images)
WHERE {
  VALUES (?gnd ?writer) {
    (<http://d-nb.info/gnd/118546937> "Gerhart Hauptmann")
    (<http://d-nb.info/gnd/118642375> "George Bernard Shaw")
    (<http://d-nb.info/gnd/118577166> "Thomas Mann")
  }
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?image ?link ?gnd ;
           edm:type "IMAGE" .
  }
}
GROUP BY ?writer
ORDER BY DESC(?images)
```

```shmarql
# shmarql-label: Hauptmann in the Deutsche Fotothek: stage photos vs. other images

PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?stage_photo (xsd:integer(COUNT(DISTINCT ?image)) AS ?images)
WHERE {
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?image ?link <http://d-nb.info/gnd/118546937> ;
           edm:type "IMAGE" ;
           dc:title ?title .
    ?agg edm:aggregatedCHO ?image ;
         edm:dataProvider "Deutsche Fotothek" .
    BIND(CONTAINS(LCASE(STR(?title)), "szenenbild") AS ?stage_photo)
  }
}
GROUP BY ?stage_photo
```

## Act III: A Plot Twist the Data Invented for Itself

Every good story has a twist. This graph had one — and it has nothing to do with literature.

### 🏫 The Eponym Trap

Georg Büchner looks like a titan in a title search. But 528 of the hits are — a secondary school. The Georg Büchner Gymnasium in Darmstadt, immortalized in architectural drawings by Hans Schwippert, held by the Architecture Museum of TU Munich. Büchner, dead at 23, author of barely a handful of works (*Woyzeck*: 23 objects), is so famous that his name turned into brickwork.
```shmarql
# shmarql-label: "Büchner" in the Architecture Museum of TU Munich

PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT (xsd:integer(COUNT(DISTINCT ?object)) AS ?objects) (SAMPLE(?title) AS ?example_title)
WHERE {
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?agg edm:dataProvider "Architekturmuseum der Technischen Universität München" ;
         edm:aggregatedCHO ?object .
    ?object dc:title ?title .
    FILTER(CONTAINS(LCASE(STR(?title)), "büchner"))
  }
}
```

```shmarql
# shmarql-label: Woyzeck among Büchner's works

PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT (xsd:integer(COUNT(DISTINCT ?work)) AS ?woyzeck_objects)
WHERE {
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?work dc:creator <http://d-nb.info/gnd/118516906> ;
          dc:title ?title .
    FILTER(CONTAINS(LCASE(STR(?title)), "woyzeck") || CONTAINS(LCASE(STR(?title)), "wozzeck"))
  }
}
```

Fame in its final stage: you're no longer a person, you're a school.

## Act IV: Immortality in Metal

And when paper and photograph aren't enough, there's the hardest material of fame: the medal.

In the coin cabinet (*Münzkabinett*, 79,006 objects) you find them all again, cast in bronze: Goethe (24), Schiller (9) — and right in the middle, struck in the least philosophical manner possible, Hegel. Beside them Kant, Leibniz, Marx. But above them all, with 95 medals, stands a man who never wrote a novel and yet won every Reformation anniversary: Martin Luther.

```shmarql
# shmarql-view: barchart
# shmarql-x: person
# shmarql-y: medals
# shmarql-label: Medals in the Münzkabinett, by person depicted

PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?person (xsd:integer(COUNT(DISTINCT ?medal)) AS ?medals)
WHERE {
  VALUES (?gnd ?person) {
    (<http://d-nb.info/gnd/118575449> "Martin Luther")
    (<http://d-nb.info/gnd/118540238> "Goethe")
    (<http://d-nb.info/gnd/118607626> "Schiller")
    (<http://d-nb.info/gnd/118547739> "Hegel")
    (<http://d-nb.info/gnd/118559796> "Kant")
    (<http://d-nb.info/gnd/118571249> "Leibniz")
    (<http://d-nb.info/gnd/118578537> "Marx")
  }
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?agg edm:dataProvider "Münzkabinett" ;
         edm:aggregatedCHO ?medal .
    ?medal dcterms:subject ?gnd .
  }
}
GROUP BY ?person
ORDER BY DESC(?medals)
```

Even in the coin drawer, the theologian beats the prince of poets.

## Epilogue: What the Data Knows About Fame

The graph knows no literary history. It doesn't know that *Faust* is a masterpiece and a playbill from 1832 just a scrap. It only counts. And precisely because of that, it tells a more honest story:

**Immortality is not a merit — it is a medium of transmission.**

Schiller survived as a playbill. Heine as a book. Hauptmann as a negative. Benn as a death mask. Büchner as a school building. Luther as a coin.

The classics won eternity because their work entered the public domain long ago and their plays were performed. The moderns are still waiting — Hauptmann and the Mann brothers have been free of copyright for years, yet the scanners have not caught up, and they remain visible mostly in what is said about them and photographed of them. Benn's protection ends on 1 January 2027. Whether he leaps from 2 to thousands is now up to the libraries. The data story will get a sequel.

Until then, the loveliest truth in this dataset remains: the greatest lyric poet of the 19th century never made it onto a poster — and survived anyway.

## 📊 Data Appendix: The Story's Key Figures

| Author | Era | Works (as creator) | Note |
|---|---|---:|---|
| Johann Wolfgang von Goethe | Weimar Classicism | 4,393 | *Faust* 677; ~96 % at BSB + Weimar |
| Friedrich Schiller | Weimar Classicism | 3,787 | *Wallenstein* trilogy 468, *Tell* 410; 2,141 playbills |
| Heinrich Heine | 19th c. | 541 | 31 playbills (6 %): the lyric poet off the stage |
| Georg Büchner | 19th c. | 45 | 173 objects about him; 528 drawings of the Georg Büchner Gymnasium |
| Gerhart Hauptmann | 20th c. (Nobel 1912) | 322 | 842 images, 802 from the Deutsche Fotothek |
| Thomas Mann | 20th c. (Nobel 1929) | 22 | 350 images |
| Heinrich Mann | 20th c. | 52 | 347 objects about him |
| Gottfried Benn | 20th c. | 2 | 48 objects about him |

**Cross-cutting findings:**

- Total holdings: 27,526,560 objects; largest institutions: Bavarian State Library (~3.0 m), German National Library (~2.9 m), SLUB Dresden (~1.7 m), Deutsche Fotothek (~1.4 m).
- Coin cabinet (*Münzkabinett*): 79,006 objects; medals for Luther (95), Goethe (24), Schiller (9), Leibniz (7), Marx (7), Kant (6), Hegel (4).

```shmarql
# shmarql-view: barchart
# shmarql-x: institution
# shmarql-y: objects
# shmarql-label: Largest institutions in GEMEA

PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?institution (xsd:integer(COUNT(?agg)) AS ?objects)
WHERE {
  GRAPH <https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm> {
    ?agg edm:dataProvider ?institution .
    FILTER(isLiteral(?institution))
  }
}
GROUP BY ?institution
ORDER BY DESC(?objects)
LIMIT 5
```

All figures are catalogue-dependent lower bounds: a work counts only if its record links the author's GND identifier. Mis-linked GND identifiers can also inflate counts. Counts use `dcterms:subject` only, because GEMEA carries each subject link as both `dc:subject` and `dcterms:subject`. Figures explored through an LLM client were rechecked with the queries above; counts produced by LLM-written SPARQL alone should be treated with care ([Tietz et al. 2026](https://arxiv.org/html/2609.11403), pp. 9–10).
