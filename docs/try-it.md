# See it work

The README's throwaway repository, with what the commands print. This page is written by the release tool from a
run of this release in a temporary folder, so the output is not typed by hand. Paths are relative to the folder.

The branch `change` adds two files to an empty `main`.

`app.py`:

```python
def load(path):
    try:
        return open(path).read()
    except:
        return None
```

`app.ts`:

```ts
const n = JSON.parse(raw);
// @ts-ignore
const x: number = n.name;
```

## Scan the change

```
$ brevio-core scan --diff main
app.py:4: [R5.4] Bare `except:` detected; specify explicit exception tuple
app.ts:2: [R9.2] `@ts-ignore` used; use `@ts-expect-error` with explanatory reason
brevio-core: 2 finding(s) in 2 file(s), 0 baselined, 0 silenced
Explain: brevio-core explain R5.4
2 fixable: brevio-core scan --diff main --fix --dry-run
```

The exit code is 1 while there are findings.

## See the fixes first

```
$ brevio-core scan --diff main --fix --dry-run
--- a/app.py
+++ b/app.py
@@ -1,5 +1,5 @@
 def load(path):
     try:
         return open(path).read()
-    except:
+    except Exception:
         return None
--- a/app.ts
+++ b/app.ts
@@ -1,3 +1,3 @@
 const n = JSON.parse(raw);
-// @ts-ignore
+// @ts-expect-error
 const x: number = n.name;
brevio-core: 2 fix(es) in 2 file(s) would be made; nothing written
```

Leave out `--dry-run` to write the rewrites. A rule has a fixer only when it has one correct rewrite; the other
findings are reported and left for you.
