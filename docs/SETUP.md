# PocketSlice – trin-for-trin opsætning (for alle)

Denne guide antager, at du **aldrig** har rørt Docker eller en Linux-server før. Følg den
oppefra og ned. Alt, der står i en grå boks, kan du kopiere og indsætte direkte.

> **Så kort som muligt:** PocketSlice er en lille hjemmeside, der kører på en computer hjemme
> hos dig. Telefonen åbner hjemmesiden, du uploader en STL, serveren slicer den med dine
> OrcaSlicer-profiler og sender G-koden til din printer. Tilføj hjemmesiden til hjemmeskærmen,
> og den opfører sig som en app.

---

## 0. Ordforklaring

| Ord | Betyder |
|-----|---------|
| **Server** | Computeren der kører PocketSlice døgnet rundt. En Proxmox-container, en mini-PC, en NAS eller din Windows-PC. |
| **Proxmox** | Et system til at køre "computere inde i en computer" (containere og virtuelle maskiner). |
| **Container (LXC)** | En lille, let "computer" inde i Proxmox. Vi laver én til PocketSlice. |
| **SSH** | En måde at skrive kommandoer til serveren fra din egen PC. Windows har det indbygget (PowerShell). |
| **Docker** | Pakker PocketSlice + OrcaSlicer i én kasse, så du slipper for at installere noget selv. |
| **Moonraker** | Den del af Klipper, som Mainsail/Fluidd (og PocketSlice) taler med. Kører på printeren, normalt port 7125. |
| **IP-adresse** | Serverens "adresse" på hjemmenettet, fx `192.168.1.60`. |

---

## 1. Hvad du skal bruge

- En Voron (eller anden printer) med **Klipper + Moonraker**. Hvis du bruger Mainsail eller
  Fluidd, har du det allerede.
- En **server** med x86-processor (Intel/AMD). Vi bruger en Proxmox-container i denne guide,
  men trin 3 og frem er ens for alle andre Linux-maskiner.
- Din PC med **OrcaSlicer** installeret og dine profiler sat op.
- Din telefon på samme WiFi som printeren og serveren.

**Find printerens IP-adresse nu.** Åbn Mainsail/Fluidd i browseren og kig i adresselinjen. Står
der fx `http://192.168.1.50`, er Moonraker-adressen `http://192.168.1.50:7125`. Skriv den ned.

---

## 2. Lav en container i Proxmox

Alle kommandoer i dette trin skrives i **Proxmox' egen shell**: Log ind på Proxmox' web-side
(`https://<proxmox-ip>:8006`), klik på serveren i venstre side (navnet under "Datacenter"), og
klik på **Shell** øverst til højre. Der åbner et sort vindue – det er her, du indsætter.

### 2.1 Hent en Debian-skabelon

```bash
pveam update
pveam available | grep debian-12-standard
```

Den sidste linje viser noget i stil med `debian-12-standard_12.7-1_amd64.tar.zst`. Hent den
(ret versionsnummeret, hvis dit er et andet):

```bash
pveam download local debian-12-standard_12.7-1_amd64.tar.zst
```

### 2.2 Opret containeren

Kopiér hele blokken. Ret kun tallet efter `CTID=` hvis 200 allerede er brugt, og ret
`PASSWORD=` til et kodeord, du kan huske (det er root-kodeordet til containeren).

```bash
CTID=200
PASSWORD='SkiftMigNu123'
TEMPLATE=$(ls /var/lib/vz/template/cache/ | grep debian-12-standard | head -n1)

pct create $CTID local:vztmpl/$TEMPLATE \
  --hostname pocketslice \
  --cores 4 --memory 4096 --swap 1024 \
  --rootfs local-lvm:32 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 \
  --password "$PASSWORD" \
  --onboot 1
pct start $CTID
sleep 5
pct exec $CTID -- hostname -I
```

Den sidste kommando skriver containerens **IP-adresse** (fx `192.168.1.60`). Skriv den ned – det
er din *server-IP* i resten af guiden.

> Bruger din Proxmox `local-zfs` eller en anden lagring end `local-lvm`? Ret `--rootfs local-lvm:32`
> til dit navn (ses under Datacenter → Storage). `32` er GB diskplads.

> **Hvorfor `nesting=1,keyctl=1`?** Docker inde i en container kræver de to flag. Uden dem fejler
> Docker med en uforståelig fejl.

### 2.3 Gå ind i containeren

Stadig i Proxmox-shellen:

```bash
pct enter 200
```

Prompten skifter til `root@pocketslice:~#`. Nu er du "inde i serveren". Fortsæt til trin 3.

*(Alternativ til `pct enter`: fra din PC kan du bruge SSH – se trin 7.)*

---

## 3. Installer PocketSlice (én kommando)

Indsæt denne ene linje i containeren (eller på enhver anden Debian/Ubuntu-maskine):

```bash
curl -fsSL https://raw.githubusercontent.com/westfrost/Testilento/main/scripts/install.sh | bash
```

Scriptet:

1. installerer Docker,
2. henter PocketSlice til `/opt/pocketslice`,
3. spørger om printerens Moonraker-adresse (skriv fx `http://192.168.1.50:7125` og tryk Enter),
4. bygger appen – **det tager 3–10 minutter**, fordi OrcaSlicer (ca. 150 MB) hentes,
5. starter appen og skriver adressen, du skal åbne.

Når du ser `PocketSlice is running`, er du færdig med serveren. Kører noget galt, så kig i
afsnit 9 (Fejlfinding).

Tjek at det virker – der skal komme `{"ok":true, ...}` tilbage:

```bash
curl -s http://localhost:8080/api/health
```

---

## 4. Første opsætning på telefonen (wizard)

1. Åbn browseren på telefonen og gå til **`http://<server-ip>:8080`** (fx `http://192.168.1.60:8080`).
2. Opsætningsguiden starter selv. Den har 5 trin:

   | Trin | Hvad du gør |
   |------|-------------|
   | **1 Printer** | Skriv et navn og Moonraker-adressen. Tryk **Test connection** – der skal stå *Connected*. |
   | **2 Presets** | Her skal dine OrcaSlicer-profiler ind. Se trin 5 nedenfor – det gøres nemmest fra PC'en. Du kan springe det over nu og gøre det bagefter. |
   | **3 Webcam** | Har du et kamera i Mainsail/Fluidd, står det på listen – tryk på det. Ellers bare **Continue**. |
   | **4 Password** | Sæt et kodeord. Alle der kan åbne siden, kan starte og stoppe print. |
   | **5 Done** | Tryk **Start slicing**. |

3. Guiden kan altid køres igen: **Settings → Run setup wizard**.

---

## 5. Få dine OrcaSlicer-profiler ind

PocketSlice slicer med **præcis** de profiler, du har i OrcaSlicer. Der er tre måder – vælg **A**,
hvis du kan.

### A · Upload mappen fra PC'en (anbefalet, 1 minut)

1. På din **PC** (den med OrcaSlicer): åbn Chrome eller Edge og gå til `http://<server-ip>:8080`.
2. Gå til **Settings** (tandhjulet nederst) → afsnittet **OrcaSlicer presets** → knappen
   **Choose OrcaSlicer folder…**
3. Vælg OrcaSlicers konfigurationsmappe:

   | OS | Mappe |
   |----|-------|
   | Windows | Skriv `%APPDATA%\OrcaSlicer` i adresselinjen i fil-vælgeren og tryk Enter, klik derefter **Upload/Vælg mappe** |
   | macOS | `~/Library/Application Support/OrcaSlicer` (tryk ⌘⇧G i dialogen og indsæt stien) |
   | Linux | `~/.config/OrcaSlicer` |

4. Browseren advarer måske "Upload 1.234 filer til denne side?" – tryk **Upload**. Kun
   `user/`, `system/` og `OrcaSlicer.conf` bliver brugt; resten smides væk.
5. Der står nu fx *1 printer · 4 process · 12 filament*. Færdig. **Gentag trin 3–4, hver gang du
   har ændret profiler i OrcaSlicer.**

### B · Orca Cloud (OrcaSlicer 2.4 eller nyere, eksperimentel)

Hvis du i OrcaSlicer har slået *Sync user presets* til og er logget ind med en **Orca-konto**
(ikke Bambu-konto), kan PocketSlice hente profilerne direkte fra skyen:

1. Settings → **B · Orca Cloud** → skriv e-mail og kodeord til Orca-kontoen → **Sign in**.
   Har du oprettet kontoen med Google/GitHub, så brug **Use the browser login** og følg de tre
   punkter på skærmen (du ender på en side, der ikke kan åbnes – kopiér adressen og indsæt den).
2. Tryk **Sync now**. Vælg evt. **Every hour**, så det sker automatisk.

> Orca Cloud-API'et er ikke officielt dokumenteret. Virker det ikke, så brug metode A.
> Bruger du Bambu-kontoens sync i Orca, kan det **ikke** hentes (lukket system) – brug A.

### C · Eksportér enkelte profiler (virker fra telefonen)

I OrcaSlicer: højreklik på et preset → **Export** → gem filen (`.orca_printer` /
`.orca_filament`). Send filen til telefonen og upload den under Settings → **C · Import
preset file…**

---

## 6. Slice og print

1. **Slice**-fanen → **Add a model** → vælg en STL (eller del filen til PocketSlice fra en anden app på Android).
2. Vælg **Printer / Process / Filament**. Appen husker dit valg.
3. **Shrinkage compensation**: knappen viser, hvad filament-profilen har (fx *XY 99.5 %*).
   Slå den fra, hvis du vil printe uden kompensation. Standardvalget kan sættes under Settings → Slicing defaults.
4. **Quick overrides** (valgfrit): laghøjde, infill, supports, temperaturer m.m. Tomme felter = profilens værdi.
5. Tryk **Slice**. Du får tid, gram, lag og et billede.
6. **Print now** sender filen til printeren og starter. **Send to printer** uploader kun.
7. **Printer**-fanen viser fremdrift, temperaturer, kamera og knapper til pause/stop.

**Tilføj til hjemmeskærm:** iPhone: Del-ikonet → *Føj til hjemmeskærm*. Android: menuen ⋮ →
*Installér app* / *Føj til startskærm*.

---

## 7. SSH fra din PC (i stedet for Proxmox-shellen)

Så kan du styre serveren fra PowerShell/Terminal på PC'en. Kodeordet er det, du satte i trin 2.2.

```powershell
ssh root@192.168.1.60
```

Skriv `yes` første gang. Nyttige kommandoer, når du er inde:

```bash
# Se om PocketSlice kører
docker ps

# Se log (Ctrl+C for at stoppe)
docker logs -f pocketslice

# Genstart appen
cd /opt/pocketslice && docker compose restart

# Opdater til nyeste version
bash /opt/pocketslice/scripts/install.sh

# Skift Moonraker-adresse eller port og genstart
nano /opt/pocketslice/.env
cd /opt/pocketslice && docker compose up -d
```

---

## 8. Adgang udefra (når du ikke er hjemme) – Tailscale, gratis

Åbn **aldrig** porte i routeren. Tailscale laver et privat netværk mellem dine enheder.

1. Opret en gratis konto på <https://tailscale.com> og installér Tailscale-appen på telefonen. Log ind.
2. Lav en nøgle: <https://login.tailscale.com/admin/settings/keys> → **Generate auth key** → slå
   *Reusable* til → kopiér nøglen (starter med `tskey-auth-`).
3. Slå **MagicDNS** og **HTTPS Certificates** til under <https://login.tailscale.com/admin/dns>.
4. På serveren (SSH eller `pct enter`):

   ```bash
   cd /opt/pocketslice
   nano .env
   ```

   Find linjen `TS_AUTHKEY=` og indsæt nøglen, så der står `TS_AUTHKEY=tskey-auth-....`.
   Gem med `Ctrl+O`, Enter, luk med `Ctrl+X`.

5. Start med Tailscale-opsætningen:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d --build
   ```

6. Efter et minut findes appen på **`https://pocketslice.<dit-tailnet>.ts.net`** (navnet ses i
   Tailscale-appen under *Machines*). Åbn den på telefonen og tilføj til hjemmeskærmen igen –
   den adresse virker både hjemme og ude.

> Kører Tailscale i en Proxmox-container, skal containeren have adgang til `/dev/net/tun`. Kør
> dette **i Proxmox-shellen** (ikke inde i containeren), ret `200` til dit CTID, og genstart containeren:
>
> ```bash
> echo 'lxc.cgroup2.devices.allow: c 10:200 rwm' >> /etc/pve/lxc/200.conf
> echo 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' >> /etc/pve/lxc/200.conf
> pct reboot 200
> ```

---

## 9. Fejlfinding

| Problem | Løsning |
|---------|---------|
| `Cannot reach Moonraker` / *Offline* øverst | Brug printerens **IP** i stedet for `voron.local` (Docker kan ikke altid slå `.local`-navne op). Settings → Moonraker URL → Test connection. |
| Wizarden siger *Connected*, men Files er tom | Normalt. Der ligger ingen G-kode i Moonrakers `gcodes`-mappe endnu. |
| *Preset X inherits Y which was not found* | Du har kun uploadet `user/`. Upload hele OrcaSlicer-mappen igen (metode A), så `system/` kommer med. |
| Slice fejler | Tryk **Slicer log** på jobbet. Fejlkoder oversættes (fx *Object is too large for the print bed*). |
| `docker: permission denied` / Docker starter ikke i LXC | Containeren mangler `nesting=1,keyctl=1`. I Proxmox-shellen: `pct set 200 --features nesting=1,keyctl=1 && pct reboot 200`. |
| Build fejler ved *Could not download OrcaSlicer* | Filnavnet på GitHub er ændret. Find nyeste Linux-AppImage på <https://github.com/SoftFever/OrcaSlicer/releases>, kopiér linket og kør: `cd /opt/pocketslice && docker compose build --build-arg ORCA_APPIMAGE_URL=<link> && docker compose up -d` |
| Glemt app-kodeord | På serveren: `docker exec pocketslice sh -c 'sed -i "s/\"password_hash\": \".*\"/\"password_hash\": \"\"/" /data/settings.json'` og `docker compose restart`. |
| Webcam vises ikke | Skriv hele adressen (fx `http://192.168.1.50/webcam/?action=stream`) i Settings → Webcam. |
| Telefonen kan ikke åbne `http://<ip>:8080` | Er telefonen på samme WiFi? Kører containeren (`pct list` i Proxmox)? Prøv fra PC'en først. |

Se hele loggen med `docker logs --tail 200 pocketslice`.

---

## 10. Snydeark – alt på ét sted

```bash
# --- Proxmox-shell -------------------------------------------------------
pct list                      # alle containere
pct start 200 / pct stop 200  # start/stop
pct enter 200                 # "gå ind" i containeren

# --- Inde i containeren / på serveren -------------------------------------
curl -fsSL https://raw.githubusercontent.com/westfrost/Testilento/main/scripts/install.sh | bash   # installer / opdater
docker ps                                     # kører den?
docker logs -f pocketslice                    # log
cd /opt/pocketslice && docker compose restart # genstart
cd /opt/pocketslice && docker compose down    # stop helt
nano /opt/pocketslice/.env                    # indstillinger (Moonraker, port, Tailscale-nøgle)
```

Appen: `http://<server-ip>:8080` · Settings → **Run setup wizard** kører guiden igen.
