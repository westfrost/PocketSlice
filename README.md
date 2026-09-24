# PocketSlice

**"Bambu Handy" for your Voron.** A self-hosted mobile app (PWA): drop an STL in from your
phone, get it sliced with *exactly* the OrcaSlicer presets you use on your PC, and send the
G-code straight to Klipper/Moonraker. Then follow the print, control temperatures, pause, watch
the webcam and run macros, all from the phone.

```
 Phone (PWA)  ──HTTPS──▶  PocketSlice (Docker)  ──HTTP──▶  Moonraker / Klipper (Voron)
                          ├─ FastAPI backend
                          ├─ OrcaSlicer CLI (headless)
                          └─ your Orca presets (user/ + system/)
```

| Tab | What it does |
|-----|--------------|
| **Slice** | Upload STL/3MF/OBJ/STEP, 3D preview, pick printer/process/filament preset (your own from Orca), one-tap shrinkage on/off, quick overrides (layer height, infill, supports, temperatures…), slice, see time/grams/layers + thumbnail, *Send* or *Print now*. |
| **Printer** | Status, progress, time left, layers, temperatures (set/off), speed/flow/fan sliders, pause/resume/cancel, home, G-code, E-STOP, your Klipper macros as buttons, webcam. |
| **Files** | G-code files on the printer with thumbnails: print again, view metadata, delete. |
| **Settings** | Moonraker URL/API key, webcam (auto-discovered from Moonraker), default presets, shrinkage default, accent colour, password, preset sources (folder upload, Orca Cloud, import). |

Extras: a setup wizard on first start, "Share → PocketSlice" from Android's file manager or
browser (PWA share target), optional password set from the app.

**New here? Follow the detailed guide: [docs/SETUP.md](docs/SETUP.md)** (Proxmox, SSH, phone,
presets, Tailscale, with copy-paste commands).

---

## Requirements

* A machine on your home network with **Docker**: a Proxmox LXC/VM, a mini PC, a NAS, an old
  laptop or your Windows PC with Docker Desktop. x86_64 is the safe choice; OrcaSlicer 2.4+ also
  ships arm64 AppImages, which the Dockerfile tries to fetch on aarch64 machines (untested).
* Moonraker on the printer (you already have it if you use Mainsail/Fluidd).
* A copy of your OrcaSlicer configuration folder (see below).

## Quick start

One command on a Debian/Ubuntu machine (e.g. a Proxmox LXC container with `nesting=1`).
A fresh Debian container has no `curl`, so install it first:

```bash
apt-get update && apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/westfrost/PocketSlice/main/scripts/install.sh | sudo bash
```

Or by hand:

```bash
git clone https://github.com/westfrost/PocketSlice.git pocketslice && cd pocketslice
cp .env.example .env            # set MOONRAKER_URL
docker compose up -d --build    # the first build downloads OrcaSlicer (~150 MB) and takes a few minutes
```

Open `http://<server-ip>:8080` on the phone. The setup wizard starts automatically (printer,
presets, webcam, password). Add the page to the home screen ("Add to Home Screen" in Safari /
"Install app" in Chrome) so it opens like a real app.

## Your OrcaSlicer presets

PocketSlice slices with the same JSON presets as OrcaSlicer on your PC. User presets in Orca only
store *your changes* plus an `inherits` field pointing at a system preset, so the app needs both
`user/` and `system/` to "flatten" a preset into a complete config for the CLI.

**Easiest:** open the app in Chrome/Edge on the PC → Settings → *Choose OrcaSlicer folder…* and
select OrcaSlicer's configuration folder. The browser uploads `user/`, `system/` and
`OrcaSlicer.conf` directly; nothing to install. Repeat whenever you change presets.

| OS | OrcaSlicer configuration |
|----|--------------------------|
| Windows | `%APPDATA%\OrcaSlicer` |
| macOS | `~/Library/Application Support/OrcaSlicer` |
| Linux | `~/.config/OrcaSlicer` |

**Orca Cloud (OrcaSlicer 2.4+, experimental):** if you enabled *Sync user presets* with an Orca
account, the app can sign in (email/password or browser login) and pull presets from
`cloud.orcaslicer.com`, manually or automatically every 15 min/hour. The API was derived from
OrcaSlicer's source (`OrcaCloudServiceAgent.cpp`) and is not official; Bambu-account sync is a
closed system and cannot be used.

**Other ways:** copy the folder (or just `user/`, `system/` and `OrcaSlicer.conf`) into
`./profiles/`, run `scripts/sync-profiles.ps1` (Windows) / `scripts/sync-profiles.sh` on a
schedule, or export single presets from Orca (right-click → *Export*) and import them in the app.
`OrcaSlicer.conf` makes the app default to the presets you last used in Orca.

## Access from outside your home (Tailscale, free)

Do **not** open router ports. Install Tailscale on the phone and on the server, then publish
the app on your tailnet with HTTPS:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
tailscale serve --bg 8080        # → https://pocketslice.<tailnet>.ts.net
```

HTTPS is also what makes PWA installation and "Share to PocketSlice" work fully on Android.
`docker-compose.tailscale.yml` is an alternative that runs Tailscale as a Docker sidecar; it does
not work inside unprivileged Proxmox LXC containers.

## Slicing speed

PocketSlice runs the same slicing engine as the OrcaSlicer desktop app (the CLI inside the
AppImage), so a slice takes about as long as it does on a PC with the same CPU. It is
multi-threaded and scales well with cores; give the container as many as you can spare.

Rough expectations for the slice itself (upload time excluded):

| Model | Desktop i5/i7/Ryzen 5 | N100 mini PC / 4-core LXC | Raspberry Pi 5 (arm64, untested) |
|-------|------------------------|---------------------------|----------------------------------|
| Small part, no supports | 2–5 s | 5–10 s | 20–40 s |
| Typical Voron part (100–200 MB G-code, some supports) | 10–30 s | 30–90 s | 3–6 min |
| Large/complex (full bed, organic supports, fine layers, big STL) | 1–3 min | 3–8 min | 15–30 min |

On top of that PocketSlice adds a fixed 2–5 s per job: starting the AppImage headless, writing
the 3MF and parsing the G-code. The result card shows exactly how long the slice took.
One job runs at a time; further jobs queue. `SLICE_TIMEOUT` (default 30 min) kills runaway jobs.

Tips: more cores beat a faster clock for big models; coarser layer height, fewer walls and
normal (not organic) supports slice several times faster; keep STLs reasonable (a 200 MB scan
mesh slices slowly everywhere).

## Configuration

Everything can be set in `.env` (see `.env.example`) and, except for the port and the Tailscale
key, changed in the app's Settings tab, which stores to `/data/settings.json`.

| Variable | Description |
|----------|-------------|
| `MOONRAKER_URL` | e.g. `http://voron.local:7125` (use the IP if mDNS does not work in Docker) |
| `MOONRAKER_API_KEY` | only if Moonraker requires it |
| `APP_PASSWORD` | fallback app password; normally set from the wizard/Settings instead |
| `WEBCAM_STREAM_URL` / `WEBCAM_SNAPSHOT_URL` | MJPEG stream/snapshot; can be auto-filled from Moonraker |
| `SLICE_TIMEOUT` | seconds before a slicing job is killed (1800) |
| `ORCA_VERSION` (build arg) | OrcaSlicer version fetched in the Docker build (2.4.1). `ORCA_APPIMAGE_URL` overrides the URL. |

## Troubleshooting

* **"Preset X inherits Y which was not found"**: upload Orca's `system/` folder too (folder
  upload does this), or export the preset from Orca and import it.
* **Slicing fails**: tap *Slicer log* on the job; the full OrcaSlicer output is kept. Exit codes
  are translated to plain text (e.g. "Object is too large for the print bed").
* **Cannot reach Moonraker from the container**: use the printer's IP instead of `voron.local`
  (`.local` names do not resolve inside the container). Test with *Test connection* in Settings.
* **`open sysctl net.ipv4.ip_unprivileged_port_start … permission denied` on start**: Docker's
  bridge networking is blocked in unprivileged Proxmox LXC containers. The compose file uses
  `network_mode: host` for that reason; do not add `ports:` or a custom network to it.
* **Docker build fails downloading OrcaSlicer**: release file names change now and then. Find
  the newest Linux AppImage at <https://github.com/SoftFever/OrcaSlicer/releases> and build with
  `docker compose build --build-arg ORCA_APPIMAGE_URL=<url>`.
* **Webcam does not show**: Moonraker's relative URL (`/webcam/?action=stream`) is mapped to the
  printer's port 80. Enter the full URL in Settings if your setup differs.

## Development

```bash
cd backend && pip install -r requirements.txt pytest anyio && python -m pytest   # 25 tests
./scripts/dev.sh                     # runs the app with a fake slicer + sample presets on :8080
```

* `backend/app/profiles.py` – finds Orca presets and flattens the inherits chain.
* `backend/app/slicer.py` – runs `orca-slicer --load-settings … --load-filaments … --slice 0`,
  finds the G-code (in outputdir or inside the 3MF) and reads time/filament/thumbnail.
* `backend/app/moonraker.py` – small async Moonraker client.
* `backend/app/orca_cloud.py` – Orca Cloud login (Supabase PKCE/password) and `sync/pull`.
* `frontend/` – plain HTML/CSS/JS, no build step; three.js is vendored for the STL preview.
* `scripts/install.sh` – one-shot installer (Docker + clone + build) for Debian/Ubuntu/Proxmox.
* `docker-compose.tailscale.yml` – optional Tailscale sidecar with `tailscale serve` for HTTPS.

## Security

The app is meant for your home network or tailnet. Set a password if others can reach it.
Webcam and Moonraker URLs can be changed by anyone who is logged in and are fetched by the
server, so do not share the app with people you do not trust.
