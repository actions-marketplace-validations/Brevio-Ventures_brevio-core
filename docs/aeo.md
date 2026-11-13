# Scoring a site for answer engines: `brevio-core aeo`

| In Plain English (For Anyone) | Technical Contract (For Engineers) |
| --- | --- |
| **What it does:** Grades your built website HTML, `robots.txt`, and `llms.txt` so AI search engines (Perplexity, ChatGPT, Claude) can discover and cite your pages accurately.<br>**When to use it:** On your static build folder (`out/`, `dist/`, `.next/`) locally or in CI. | **Command:** `brevio-core aeo <folder> [--min SCORE]`<br>**Inputs:** Built `.html` files, `robots.txt`, `llms.txt`, `skill.md`<br>**Exit Codes:** The exit code is 0 when nothing is found, 1 when not. |

`brevio-core aeo` scores how well the pages of a built site can be found, read and cited by answer engines (AEO). It is
a separate command from the pull-request gate and reads a folder of built HTML pages:

```sh
brevio-core aeo out/                      # a scorecard of every page, worst first
brevio-core aeo out/ --min 75             # exit 1 when a page scores below 75
brevio-core aeo out/ --format markdown    # a table for a job summary or a pull request comment
brevio-core aeo out/pricing.html --format json
```

## What it reads

- Every `.html` page under the folder, hidden folders and `node_modules` left out; or the one page you name.
- At the root of the folder (for one page, its own folder): `robots.txt`, `llms.txt`, `skill.md` and
  `.well-known/agent-card.json`. A missing file counts as missing, not as an error.
- A Markdown copy beside a page (`page.md` or `page.html.md`), which counts as the page's Markdown mirror.

## It reads local files only

`aeo` does not fetch anything. Given a URL (an address with a scheme such as `https:`, or a bare host name such as
`www.example.com`) it stops with exit code 2 and says what to do instead:

1. Save the built page: your site's build output folder is best; a browser's "Save page as" works for one page.
2. Put the site's `robots.txt` and `llms.txt` beside it.
3. Run `brevio-core aeo` on that folder.


## The scorecard

Each page gets a score out of 100 and a grade from A to F, from five parts:

| Part | What it looks at |
| --- | --- |
| Discovery | whether `robots.txt` shuts out the AI crawlers, `llms.txt` and its per-page token counts, a canonical URL |
| Structure | text a crawler sees without running scripts, a heading tree, a table, JSON-LD structured data, enough text to quote |
| Token economics | how much of the page is markup, the page's size, a Markdown copy |
| Capability signals | `skill.md`, an agent card or an MCP descriptor |
| A link for people | a one-click link a human reader can follow from a cited answer |

Under each page come its gaps, the first ones first. The text and Markdown reports start with what the site files say;
`--format json` adds each part's score.

## Exit codes

| Code | When |
| --- | --- |
| 0 | every page scores `--min` or more (the default `--min` is 0: report only) |
| 1 | at least one page scores below `--min` |
| 2 | a usage error: a URL, a missing path, a folder with no `.html` page, or a bad `--min` |

## In CI

Run it after the build step, on the build output:

```yaml
      - run: npm run build
      - run: pip install brevio-core==0.10.0
      - run: brevio-core aeo out/ --min 75 --format markdown >> "$GITHUB_STEP_SUMMARY"
```
