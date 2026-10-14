# docmap (copy inside this project)

This folder is a copy of [docmap](https://github.com/Muzasio/docmap): it draws this project's code as diagrams, linked to its docs and to the exact source line.

## Open it

From the project folder:

```
python3 docs/viewer/docmap/docmap.py view
```

| Also | Command |
|---|---|
| Scan only, no window | `python3 docs/viewer/docmap/docmap.py scan .` |
| Check what is installed | `python3 docs/viewer/docmap/docmap.py setup` |

Settings for this project: `docs/viewer/docmap.json` (for example `{"ignore": ["profiles"]}`).

## Learn more, get updates

- How to read the diagrams: [user guide](https://github.com/Muzasio/docmap/blob/main/docs/USER-GUIDE.md)
- Every command, setting and limit: [reference](https://github.com/Muzasio/docmap/blob/main/docs/REFERENCE.md)
- Newer version: copy the files from the docmap repo over this folder. Your settings and edits in `docs/viewer/` are kept.
