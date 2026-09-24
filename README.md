# PocketSlice

**"Bambu Handy" til din Voron.** En selv-hostet mobil-app (PWA), hvor du smider en STL ind
fra telefonen, får den slicet med *præcis* de OrcaSlicer-profiler du bruger på din PC, og sender
G-koden direkte til Klipper/Moonraker. Bagefter følger du printet, styrer temperaturer, pauser,
ser webcam og kører macros – alt sammen fra telefonen.

```
 Telefon (PWA)  ──HTTPS──▶  PocketSlice (Docker)  ──HTTP──▶  Moonraker / Klipper (Voron)
                             ├─ FastAPI backend
                             ├─ OrcaSlicer CLI (headless)
                             └─ dine Orca-profiler (user/ + system/)
```

| Fane | Hvad den gør |
|------|--------------|
| **Slice** | Upload STL/3MF/OBJ/STEP, 3D-preview, vælg printer/proces/filament-preset (dine egne fra Orca), quick overrides (laghøjde, infill, supports, temperaturer …), slice, se tid/gram/lag + thumbnail, *Send* eller *Print now*. |
| **Printer** | Status, fremdrift, resterende tid, lag, temperaturer (sæt/sluk), speed/flow/fan-sliders, pause/resume/cancel, home, G-kode, E-STOP, dine Klipper-macros som knapper, webcam. |
| **Files** | G-kode-filer på printeren med thumbnails: print igen, se metadata, slet. |
| **Settings** | Moonraker-URL/API-key, webcam (auto-opdages fra Moonraker), standard-presets, rescan/import af profiler. |

Ekstra: "Del → PocketSlice" fra Android-filhåndteringen/browseren uploader STL'en direkte
(PWA share target). Der er valgfrit password på appen.

---

## Krav

* En maskine på hjemmenettet med **Docker** og **x86_64** CPU (mini-PC, NAS, gammel laptop,
  eller din Windows-PC med Docker Desktop). OrcaSlicers Linux-build findes kun til x86_64, så en
  Raspberry Pi (arm64) kan **ikke** køre sliceren selv. Klipper-hosten rører vi ikke ved.
* Moonraker på Voron'en (det har du allerede, hvis du bruger Mainsail/Fluidd).
* En kopi af din OrcaSlicer-konfigurationsmappe (se nedenfor).

## Hurtig start

```bash
git clone <dette repo> pocketslice && cd pocketslice
cp .env.example .env            # sæt MOONRAKER_URL og gerne APP_PASSWORD
# kopier dine Orca-profiler ind (se næste afsnit) – eller gør det bagefter
docker compose up -d --build    # første build henter OrcaSlicer (~150 MB) og tager nogle minutter
```

Åbn `http://<server-ip>:8080` på telefonen → *Settings* → *Test connection* → gem.
Tilføj siden til hjemmeskærmen ("Add to Home Screen" i Safari / "Install app" i Chrome), så den
åbner som en rigtig app.

## Dine OrcaSlicer-profiler

PocketSlice slicer med de samme JSON-presets som OrcaSlicer på din PC. Brugerpresets i Orca
gemmer kun *dine ændringer* + et `inherits`-felt, der peger på et systempreset, så appen skal
have både `user/` og `system/` for at kunne "flade" profilen ud til en komplet config til CLI'en.

Kopier hele mappen (eller kun `user/`, `system/` og `OrcaSlicer.conf`) til `./profiles/`:

| OS | OrcaSlicer-konfiguration |
|----|--------------------------|
| Windows | `%APPDATA%\OrcaSlicer` |
| macOS | `~/Library/Application Support/OrcaSlicer` |
| Linux | `~/.config/OrcaSlicer` |

Automatisk sync fra Windows-PC'en (kør i Task Scheduler ved login, eller når du har ændret profiler):

```powershell
# til en netværksmappe/share som er containerens ./profiles
.\scripts\sync-profiles.ps1 -Destination "\\nas\pocketslice\profiles" -AppUrl http://nas:8080
# eller over SSH
.\scripts\sync-profiles.ps1 -Ssh user@server:/opt/pocketslice/profiles -AppUrl http://server:8080
```

`scripts/sync-profiles.sh` gør det samme fra Linux/macOS. Scriptet beder til sidst appen om at
rescanne, ellers tryk *Rescan folder* under Settings. `OrcaSlicer.conf` gør, at appen som
standard vælger de presets du sidst brugte i Orca.

Alternativt: højreklik et preset i OrcaSlicer → *Export* → upload `.orca_printer` /
`.orca_filament` / `.json` via *Import preset…* i appen.

## Adgang udefra (Tailscale – gratis)

Åbn **ikke** porte i routeren. Installér Tailscale på telefonen og brug sidecar-opsætningen,
som giver appen et eget HTTPS-navn på dit tailnet:

```bash
# .env: TS_AUTHKEY=tskey-auth-...   (Tailscale admin → Settings → Keys)
docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d --build
# → https://pocketslice.<tailnet>.ts.net
```

HTTPS er også det, der får PWA-installation og "Del til PocketSlice" til at virke fuldt ud på
Android. Alternativ: kør Tailscale direkte på server-maskinen og brug `http://<ts-ip>:8080`.

## Budget (de 100 USD)

Softwaren koster 0 kr. Pengene skal kun bruges, hvis du **ikke** har en x86-maskine, der
alligevel kører døgnet rundt:

* **~90–110 USD: brugt/ny mini-PC med Intel N100 eller lign.** (8 GB RAM, 128+ GB SSD). Slicer et
  typisk Voron-print på sekunder, kører Docker + Tailscale, bruger ~6 W. Det er den anbefalede
  brug af pengene.
* **0 USD:** din Windows-PC med Docker Desktop (skal være tændt når du slicer), eller en NAS med
  Docker (Synology/QNAP x86-modeller).
* Tailscale (personlig plan) er gratis. Ingen cloud-abonnement er nødvendigt.

## Konfiguration

Alt kan sættes i `.env` (se `.env.example`) og – bortset fra `APP_PASSWORD` – ændres i appens
Settings-fane, som gemmer i `/data/settings.json`.

| Variabel | Beskrivelse |
|----------|-------------|
| `MOONRAKER_URL` | fx `http://voron.local:7125` (brug IP hvis mDNS ikke virker i Docker) |
| `MOONRAKER_API_KEY` | kun hvis Moonraker kræver det |
| `APP_PASSWORD` | password til appen (anbefales) |
| `WEBCAM_STREAM_URL` / `WEBCAM_SNAPSHOT_URL` | MJPEG-stream/snapshot; kan auto-udfyldes fra Moonraker |
| `SLICE_TIMEOUT` | sekunder før et slice-job dræbes (1800) |
| `ORCA_VERSION` (build-arg) | OrcaSlicer-version der hentes i Docker-build (2.3.0). `ORCA_APPIMAGE_URL` overstyrer URL'en. |

## Fejlfinding

* **"Preset X inherits Y which was not found"** – kopier Orcas `system/`-mappe med, eller
  eksportér presettet fra Orca og importér det.
* **Slice fejler** – tryk *Slicer log* på jobbet; hele OrcaSlicer-outputtet gemmes. Exit-koder
  oversættes til klartekst (fx "Object is too large for the print bed").
* **Kan ikke nå Moonraker fra containeren** – brug printerens IP i stedet for `voron.local`,
  eller sæt `extra_hosts` i `docker-compose.yml`. Test med *Test connection* i Settings.
* **Docker-build fejler ved download af OrcaSlicer** – filnavnene på GitHub-releases skifter
  af og til. Find den nyeste Linux-AppImage på
  <https://github.com/SoftFever/OrcaSlicer/releases> og byg med
  `docker compose build --build-arg ORCA_APPIMAGE_URL=<url>`.
* **Webcam vises ikke** – Moonrakers relative URL (`/webcam/?action=stream`) oversættes til
  printerens port 80. Skriv den fulde URL i Settings, hvis din opsætning er anderledes.

## Udvikling

```bash
cd backend && pip install -r requirements.txt pytest anyio && python -m pytest   # 20 tests
./scripts/dev.sh                     # kører appen med en falsk slicer + eksempelprofiler på :8080
```

* `backend/app/profiles.py` – finder og flader Orca-presets ud (inherits-kæden).
* `backend/app/slicer.py` – kører `orca-slicer --load-settings … --load-filaments … --slice 0`,
  finder G-koden (i outputdir eller inde i 3MF'en) og læser tid/filament/thumbnail ud.
* `backend/app/moonraker.py` – lille async Moonraker-klient.
* `frontend/` – ren HTML/CSS/JS uden build-step; three.js er vendoret til STL-preview.
* `docker-compose.tailscale.yml` – valgfri Tailscale-sidecar med `tailscale serve` for HTTPS.

## Sikkerhed

Appen er tænkt til hjemmenettet/tailnet. Sæt `APP_PASSWORD`, hvis andre kan nå den. Webcam-
og Moonraker-URL'er kan ændres af enhver, der er logget ind, og hentes af serveren – så del ikke
appen med folk, du ikke stoler på.
