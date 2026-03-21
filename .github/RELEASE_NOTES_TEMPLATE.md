# Release Notes Style Guide

Reference for writing GitHub Release notes after `cz bump`.

## Format

```
This release [what changed in plain language].

### New

- **[key phrase]** for `tool_name` or `ed command` [rest of description]

### Improved

- **[key phrase]** in `tool_name` [rest of description]

### Fixed

- Fix **[key phrase]** in `tool_name` [rest of description]

### Upgrade Notes

Drop-in upgrade, no changes needed.
```

## Rules

- Summary line starts with "This release..."
- Sections: New, Improved, Fixed, Upgrade Notes
- Omit empty sections (except Upgrade Notes, always include)
- Start each bullet with a verb (Add, Fix, Support, Improve, Drop, Allow, Remove)
- Bold the key phrase in each bullet
- Backtick tool/command names (`list_threads`, `ed threads get`)
- One line per bullet
- No em dashes, no commit hashes, no PR links
- Omit internal-only changes (refactors, test fixes, CI)
- If only internal changes: "This release includes internal optimisations." + "Drop-in upgrade, no changes needed."

## Breaking changes

- **Previously:** [old behaviour]
- **Now:** [new behaviour]
- **Migration:** [what to do]
