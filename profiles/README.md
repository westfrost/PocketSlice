# OrcaSlicer presets

Put a copy of your OrcaSlicer configuration folder in here so PocketSlice slices
with exactly the same presets as your PC:

```
profiles/
  OrcaSlicer.conf          <- optional, remembers your last-used presets
  user/<id>/machine/*.json
  user/<id>/process/*.json
  user/<id>/filament/*.json
  system/<Vendor>/...      <- system presets your user presets inherit from
```

Where OrcaSlicer keeps it:

| OS      | Path                                      |
|---------|-------------------------------------------|
| Windows | `%APPDATA%\OrcaSlicer`                    |
| macOS   | `~/Library/Application Support/OrcaSlicer` |
| Linux   | `~/.config/OrcaSlicer`                    |

Use `scripts/sync-profiles.ps1` (Windows) or `scripts/sync-profiles.sh` to copy
it here automatically, then tap **Rescan folder** under Settings in the app.
