# PocketSlice – step-by-step setup (for everyone)

This guide assumes you have **never** touched Docker or a Linux server. Follow it top to bottom.
Everything in a grey box can be copied and pasted as-is.

> **The short version:** PocketSlice is a small website that runs on a computer in your home.
> Your phone opens the website, you upload an STL, the server slices it with your OrcaSlicer
> presets and sends the G-code to your printer. Add the website to your home screen and it
> behaves like an app.

---

## 0. Glossary

| Word | Meaning |
|------|---------|
| **Server** | The computer that runs PocketSlice around the clock. A Proxmox container, a mini PC, a NAS or your Windows PC. |
| **Proxmox** | Software for running "computers inside a computer" (containers and virtual machines). |
| **Container (LXC)** | A small, lightweight "computer" inside Proxmox. We create one for PocketSlice. |
| **SSH** | A way to type commands into the server from your own PC. Windows has it built in (PowerShell). |
| **Docker** | Packs PocketSlice + OrcaSlicer into one box so you never install anything by hand. |
| **Moonraker** | The part of Klipper that Mainsail/Fluidd (and PocketSlice) talk to. Runs on the printer, normally on port 7125. |
| **IP address** | The server's "address" on your home network, e.g. `192.168.1.60`. |

---

## 1. What you need

- A Voron (or any printer) running **Klipper + Moonraker**. If you use Mainsail or Fluidd you already have this.
- A **server** with an x86 CPU (Intel/AMD). This guide uses a Proxmox container, but step 3
  onwards is identical on any other Linux machine.
- Your PC with **OrcaSlicer** installed and your presets set up.
- Your phone on the same Wi-Fi as the printer and the server.

**Find your printer's IP address now.** Open Mainsail/Fluidd in the browser and look at the
address bar. If it says `http://192.168.1.50`, the Moonraker address is
`http://192.168.1.50:7125`. Write it down.

---

## 2. Create a container in Proxmox

All commands in this step are typed into **Proxmox's own shell**: log in to the Proxmox web page
(`https://<proxmox-ip>:8006`), click your node on the left (the name under "Datacenter"), then
click **Shell** at the top right. A black window opens; that is where you paste.

### 2.1 Download a Debian template

```bash
pveam update
pveam available | grep debian-12-standard
```

The last line shows something like `debian-12-standard_12.7-1_amd64.tar.zst`. Download it
(adjust the version number if yours differs):

```bash
pveam download local debian-12-standard_12.7-1_amd64.tar.zst
```

### 2.2 Create the container

Copy the whole block. Only change the number after `CTID=` if 200 is already in use, and change
`PASSWORD=` to a password you can remember (it becomes the container's root password).

```bash
CTID=200
PASSWORD='ChangeMeNow123'
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

The last command prints the container's **IP address** (e.g. `192.168.1.60`). Write it down;
it is your *server IP* for the rest of this guide.

> Does your Proxmox use `local-zfs` or another storage name instead of `local-lvm`? Change
> `--rootfs local-lvm:32` to your storage name (see Datacenter → Storage). `32` is GB of disk.

> **Why `nesting=1,keyctl=1`?** Docker inside a container needs those two flags. Without them
> Docker fails with a confusing error.

> **More cores = faster slicing.** `--cores 4` is a good start; give it 6–8 if your Proxmox host
> has them to spare. See "Slicing speed" in the README.

### 2.3 Enter the container

Still in the Proxmox shell:

```bash
pct enter 200
```

The prompt changes to `root@pocketslice:~#`. You are now "inside the server". Continue with step 3.

*(Alternative to `pct enter`: use SSH from your PC, see step 7.)*

---

## 3. Install PocketSlice

### 3.1 Install `curl` first (a fresh Debian container does not have it)

Paste this inside the container. It updates the package list and installs `curl`, the tool that
downloads the installer:

```bash
apt-get update && apt-get install -y curl
```

If you see `curl: command not found` anywhere later, this step was skipped.

### 3.2 Run the installer (one command)

Paste this single line inside the container (or on any other Debian/Ubuntu machine):

```bash
curl -fsSL https://raw.githubusercontent.com/westfrost/PocketSlice/main/scripts/install.sh | bash
```

The script:

1. installs Docker,
2. downloads PocketSlice to `/opt/pocketslice`,
3. asks for your printer's Moonraker address (type e.g. `http://192.168.1.50:7125` and press Enter),
4. builds the app – **this takes 3–10 minutes** because OrcaSlicer (about 150 MB) is downloaded,
5. starts the app and prints the address to open.

When you see `PocketSlice is running`, the server part is done. If something goes wrong, see
section 9 (Troubleshooting).

Check that it works – you should get `{"ok":true, ...}` back:

```bash
curl -s http://localhost:8080/api/health
```

---

## 4. First-time setup on the phone (wizard)

1. Open the browser on your phone and go to **`http://<server-ip>:8080`** (e.g. `http://192.168.1.60:8080`).
2. The setup wizard starts by itself. It has 5 steps:

   | Step | What you do |
   |------|-------------|
   | **1 Printer** | Type a name and the Moonraker address. Tap **Test connection**; it must say *Connected*. |
   | **2 Presets** | Your OrcaSlicer presets go in here. See step 5 below; it is easiest from the PC. You can skip it now and do it later. |
   | **3 Webcam** | If Mainsail/Fluidd has a camera, it is listed; tap it. Otherwise just **Continue**. |
   | **4 Password** | Set a password. Anyone who can open the page can start and stop prints. |
   | **5 Done** | Tap **Start slicing**. |

3. The wizard can be run again at any time: **Settings → Run setup wizard**.

---

## 5. Get your OrcaSlicer presets in

PocketSlice slices with **exactly** the presets you have in OrcaSlicer. There are three ways;
pick **A** if you can.

### A · Upload the folder from your PC (recommended, 1 minute)

1. On your **PC** (the one with OrcaSlicer): open Chrome or Edge and go to `http://<server-ip>:8080`.
2. Go to **Settings** (the gear at the bottom) → section **OrcaSlicer presets** → button
   **Choose OrcaSlicer folder…**
3. Select OrcaSlicer's configuration folder:

   | OS | Folder |
   |----|--------|
   | Windows | Type `%APPDATA%\OrcaSlicer` in the file picker's address bar, press Enter, then click **Upload / Select folder** |
   | macOS | `~/Library/Application Support/OrcaSlicer` (press ⌘⇧G in the dialog and paste the path) |
   | Linux | `~/.config/OrcaSlicer` |

4. The browser may warn "Upload 1,234 files to this site?"; click **Upload**. Only `user/`,
   `system/` and `OrcaSlicer.conf` are used; everything else is discarded.
5. It now says e.g. *1 printer · 4 process · 12 filament*. Done. **Repeat steps 3–4 whenever you
   change presets in OrcaSlicer.**

### B · Orca Cloud (OrcaSlicer 2.4 or newer, experimental)

If you enabled *Sync user presets* in OrcaSlicer and are signed in with an **Orca account**
(not a Bambu account), PocketSlice can pull the presets straight from the cloud:

1. Settings → **B · Orca Cloud** → enter the Orca account's email and password → **Sign in**.
   If you created the account with Google/GitHub, use **Use the browser login** and follow the
   three points on screen (you end up on a page that cannot load; copy its address and paste it).
2. Tap **Sync now**. Optionally choose **Every hour** to keep it automatic.

> The Orca Cloud API is not officially documented. If it does not work, use method A.
> If you use Bambu account sync in Orca it **cannot** be pulled (closed system); use A.

### C · Export single presets (works from the phone)

In OrcaSlicer: right-click a preset → **Export** → save the file (`.orca_printer` /
`.orca_filament`). Send the file to your phone and upload it under Settings → **C · Import
preset file…**

---

## 6. Slice and print

1. **Slice** tab → **Add a model** → pick an STL (on Android you can also share a file to PocketSlice from any app).
2. Choose **Printer / Process / Filament**. The app remembers your choice.
3. **Shrinkage compensation**: the switch shows what the filament preset contains (e.g. *XY 99.5 %*).
   Turn it off to print without compensation. The default can be set under Settings → Slicing defaults.
4. **Quick overrides** (optional): layer height, infill, supports, temperatures, etc. Empty fields = the preset's value.
5. Tap **Slice**. You get print time, grams, layers and a picture, plus how long the slice took.
6. **Print now** sends the file to the printer and starts. **Send to printer** only uploads.
7. The **Printer** tab shows progress, temperatures, camera and pause/stop buttons.

**Add to home screen:** iPhone: Share icon → *Add to Home Screen*. Android: menu ⋮ →
*Install app* / *Add to Home screen*.

---

## 7. SSH from your PC (instead of the Proxmox shell)

Lets you control the server from PowerShell/Terminal on the PC. The password is the one you set
in step 2.2.

```powershell
ssh root@192.168.1.60
```

Type `yes` the first time. Useful commands once you are in:

```bash
# Is PocketSlice running?
docker ps

# Show the log (Ctrl+C to stop)
docker logs -f pocketslice

# Restart the app
cd /opt/pocketslice && docker compose restart

# Update to the latest version
bash /opt/pocketslice/scripts/install.sh

# Change the Moonraker address or port, then restart
nano /opt/pocketslice/.env
cd /opt/pocketslice && docker compose up -d
```

---

## 8. Access from outside your home – Tailscale, free

**Never** open ports on your router. Tailscale creates a private network between your devices.

1. Create a free account at <https://tailscale.com> and install the Tailscale app on your phone. Sign in.
2. Create a key: <https://login.tailscale.com/admin/settings/keys> → **Generate auth key** →
   enable *Reusable* → copy the key (starts with `tskey-auth-`).
3. Enable **MagicDNS** and **HTTPS Certificates** at <https://login.tailscale.com/admin/dns>.
4. On the server (SSH or `pct enter`):

   ```bash
   cd /opt/pocketslice
   nano .env
   ```

   Find the line `TS_AUTHKEY=` and paste the key so it reads `TS_AUTHKEY=tskey-auth-....`.
   Save with `Ctrl+O`, Enter, and exit with `Ctrl+X`.

5. Start with the Tailscale configuration:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d --build
   ```

6. After about a minute the app is at **`https://pocketslice.<your-tailnet>.ts.net`** (the name
   is shown in the Tailscale app under *Machines*). Open it on the phone and add it to the home
   screen again; that address works both at home and away.

> If Tailscale runs inside a Proxmox container, the container needs access to `/dev/net/tun`.
> Run this **in the Proxmox shell** (not inside the container), change `200` to your CTID, and
> reboot the container:
>
> ```bash
> echo 'lxc.cgroup2.devices.allow: c 10:200 rwm' >> /etc/pve/lxc/200.conf
> echo 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' >> /etc/pve/lxc/200.conf
> pct reboot 200
> ```

---

## 9. Troubleshooting

| Problem | Fix |
|---------|-----|
| `Cannot reach Moonraker` / *Offline* at the top | Use the printer's **IP** instead of `voron.local` (Docker cannot always resolve `.local` names). Settings → Moonraker URL → Test connection. |
| Wizard says *Connected* but Files is empty | Normal. There is no G-code in Moonraker's `gcodes` folder yet. |
| *Preset X inherits Y which was not found* | You only uploaded `user/`. Upload the whole OrcaSlicer folder again (method A) so `system/` comes along. |
| Slicing fails | Tap **Slicer log** on the job. Error codes are translated (e.g. *Object is too large for the print bed*). |
| `curl: command not found` | Run `apt-get update && apt-get install -y curl` first (step 3.1). |
| `docker: permission denied` / Docker will not start in the LXC | The container is missing `nesting=1,keyctl=1`. In the Proxmox shell: `pct set 200 --features nesting=1,keyctl=1 && pct reboot 200`. |
| Build fails with *Could not download OrcaSlicer* | The file name on GitHub changed. Find the newest Linux AppImage at <https://github.com/SoftFever/OrcaSlicer/releases>, copy the link and run: `cd /opt/pocketslice && docker compose build --build-arg ORCA_APPIMAGE_URL=<link> && docker compose up -d` |
| Forgot the app password | On the server: `docker exec pocketslice sh -c 'sed -i "s/\"password_hash\": \".*\"/\"password_hash\": \"\"/" /data/settings.json'` then `docker compose restart`. |
| Webcam does not show | Enter the full address (e.g. `http://192.168.1.50/webcam/?action=stream`) under Settings → Webcam. |
| The phone cannot open `http://<ip>:8080` | Is the phone on the same Wi-Fi? Is the container running (`pct list` in Proxmox)? Try from the PC first. |
| Slicing is slow | Give the container more cores (`pct set 200 --cores 8`) and check nothing else hogs the Proxmox host. See "Slicing speed" in the README. |

See the full log with `docker logs --tail 200 pocketslice`.

---

## 10. Cheat sheet – everything in one place

```bash
# --- Proxmox shell --------------------------------------------------------
pct list                      # all containers
pct start 200 / pct stop 200  # start/stop
pct enter 200                 # "step into" the container

# --- Inside the container / on the server ---------------------------------
apt-get update && apt-get install -y curl      # only once, on a fresh container
curl -fsSL https://raw.githubusercontent.com/westfrost/PocketSlice/main/scripts/install.sh | bash   # install / update
docker ps                                     # is it running?
docker logs -f pocketslice                    # log
cd /opt/pocketslice && docker compose restart # restart
cd /opt/pocketslice && docker compose down    # stop completely
nano /opt/pocketslice/.env                    # settings (Moonraker, port, Tailscale key)
```

The app: `http://<server-ip>:8080` · Settings → **Run setup wizard** runs the guide again.
