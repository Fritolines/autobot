# Deploying Autobot to a Hetzner CAX11 VPS

Complete step-by-step guide. Follow in order.

---

## 1. Provision the VPS on Hetzner

1. Log in to [console.hetzner.cloud](https://console.hetzner.cloud)
2. Click **+ New Project** (or use an existing one)
3. Click **Add Server**
4. Choose:
   - **Location**: pick the closest to you (e.g. Nuremberg, Helsinki)
   - **Image**: **Ubuntu 24.04**
   - **Type**: **Shared ARM64 → CAX11** (2 vCPU, 4 GB RAM)
   - **SSH Keys**: click **Add SSH Key**, paste your public key (`cat ~/.ssh/id_ed25519.pub` on your local machine). If you don't have one, run `ssh-keygen -t ed25519` first.
   - **Firewall**: leave default for now (we'll configure it in a moment)
   - **Name**: `autobot` (or whatever you like)
5. Click **Create & Buy**
6. Note the server's **IPv4 address** shown in the console — you'll need it throughout.

---

## 2. SSH into the Server

```bash
ssh root@<YOUR_VPS_IP>
```

Accept the host key fingerprint prompt (`yes`). You should be logged in as root.

---

## 3. Update the System

```bash
apt update && apt upgrade -y && apt install -y git
```

This takes 1–3 minutes. When done, reboot:

```bash
reboot
```

Wait 20 seconds, then reconnect:

```bash
ssh root@<YOUR_VPS_IP>
```

---

## 4. Configure the Firewall

Allow SSH (so you don't lock yourself out), then block everything else inbound:

```bash
ufw allow OpenSSH
ufw --force enable
ufw status
```

Expected output shows port 22 allowed. Port 8000 is **not** opened — the dashboard is accessed via SSH tunnel (see step 11).

---

## 5. Install Docker

Use Docker's official install script:

```bash
curl -fsSL https://get.docker.com | sh
```

Verify Docker is running:

```bash
docker --version
docker compose version
```

You should see something like `Docker version 27.x.x` and `Docker Compose version v2.x.x`.

### Make Docker start automatically on reboot

```bash
systemctl enable docker
systemctl start docker
```

> **Why this matters**: `docker compose` services use `restart: unless-stopped`, which means Docker will restart all containers automatically when it starts — including after a server reboot. The `systemctl enable docker` step is what ties Docker itself to the boot process.

---

## 6. Clone the Repository

```bash
cd /opt
git clone https://github.com/<YOUR_USERNAME>/autobot.git
cd autobot
```

Replace `<YOUR_USERNAME>` with your GitHub username. If the repo is private, you'll need to authenticate — easiest is a GitHub Personal Access Token:

```bash
git clone https://github.com/<YOUR_USERNAME>/autobot.git
# when prompted for password, paste your Personal Access Token
```

---

## 7. Create the `.env` File

This file holds all secrets. It is never committed to git.

```bash
cp .env.example .env
nano .env
```

Fill in every value:

```
BOT_MODE=live
KRAKEN_API_KEY=<your Kraken API key>
KRAKEN_API_SECRET=<your Kraken API secret>
TELEGRAM_BOT_TOKEN=<your Telegram bot token>
TELEGRAM_CHAT_ID=<your Telegram chat ID>
```

**How to get your Telegram credentials**:
- **Bot token**: Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → follow prompts → copy the token
- **Chat ID**: Message [@userinfobot](https://t.me/userinfobot) on Telegram → it replies with your chat ID

**How to get your Kraken API credentials**:
- Log into Kraken → Account → Security → API → Generate New Key
- Required permissions: **Query Funds**, **Query Open Orders & Trades**, **Create & Modify Orders**, **Cancel/Close Orders**

Save and exit nano: `Ctrl+O`, `Enter`, `Ctrl+X`.

Lock down permissions so only root can read the file:

```bash
chmod 600 .env
```

---

## 8. Create Directories for Persistent Data

Docker mounts `./data` and `./logs` into the container. Create them now:

```bash
mkdir -p data logs
```

---

## 9. Build and Start the Stack

```bash
docker compose up --build -d
```

- `--build` compiles the Docker images (takes 2–5 minutes on first run — it's installing numpy, pandas, ccxt)
- `-d` runs everything in the background

After it completes, check that both containers are running:

```bash
docker compose ps
```

Expected output:

```
NAME                IMAGE            STATUS
autobot             autobot-bot      Up X seconds (health: starting)
autobot-watchdog    autobot-watchdog Up X seconds
```

After ~30 seconds the health status will change to `(healthy)`.

---

## 10. Verify Everything is Working

### Check bot logs (live tail)

```bash
docker compose logs -f bot
```

You should see the bot starting, connecting to Kraken, and entering its polling loop. Press `Ctrl+C` to stop tailing.

### Check the log file on disk

```bash
tail -f logs/autobot_live.log
```

### Check watchdog is watching

```bash
docker compose logs watchdog
```

Should show: `Watchdog started. Monitoring 'autobot' container...`

### Check container health

```bash
docker inspect --format='{{.State.Health.Status}}' autobot
```

Should return `healthy`.

---

## 11. Access the Dashboard via SSH Tunnel

The dashboard binds to `127.0.0.1:8000` on the VPS (not exposed publicly). To view it in your local browser:

**On your local machine** (not the VPS), run:

```bash
ssh -L 8000:localhost:8000 root@<YOUR_VPS_IP> -N
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

The `-N` flag keeps the tunnel open without running any command. Press `Ctrl+C` to close the tunnel when done.

**Tip — make it a one-liner alias**: add this to your local `~/.bashrc` or `~/.zshrc`:

```bash
alias autobot-dashboard="ssh -L 8000:localhost:8000 root@<YOUR_VPS_IP> -N"
```

---

## 12. Auto-restart on Reboot (Verification)

The stack is already configured to survive reboots. You can verify this:

```bash
reboot
```

Wait 30 seconds, reconnect, then check:

```bash
ssh root@<YOUR_VPS_IP>
docker compose -f /opt/autobot/docker-compose.yml ps
```

Both containers should be running.

> **How it works**: `systemctl enable docker` starts Docker when the OS boots. The `restart: unless-stopped` policy on each service in `docker-compose.yml` tells Docker to restart those containers automatically when the Docker daemon starts — as long as the container wasn't manually stopped with `docker compose stop`.

---

## Day-to-Day Operations

### View live logs

```bash
cd /opt/autobot
docker compose logs -f bot          # stdout logs
tail -f logs/autobot_live.log       # file logs (more verbose, includes DEBUG)
```

### Restart the bot

```bash
docker compose restart bot
```

### Stop everything

```bash
docker compose stop
```

This stops containers **without** removing them. Docker's restart policy will **not** bring them back automatically after a `stop` — only a reboot or `docker compose start` will.

### Start after a manual stop

```bash
docker compose start
```

### Rebuild and redeploy after a code update

```bash
git pull
docker compose up --build -d
```

Docker will rebuild the bot image and replace the running container with zero downtime (the old container stops, new one starts — brief interruption of ~5 seconds).

### View disk usage of logs

```bash
du -sh /opt/autobot/logs/
ls -lh /opt/autobot/logs/
```

Log files rotate at 10 MB, keeping 5 backups, so peak disk use is ~50 MB for logs.

### View SQLite database

```bash
apt install -y sqlite3
sqlite3 /opt/autobot/data/autobot.db ".tables"
sqlite3 /opt/autobot/data/autobot.db "SELECT * FROM trades ORDER BY exit_time DESC LIMIT 10;"
```

### Remove everything and start fresh

```bash
docker compose down -v    # stops and removes containers (data/logs volumes on disk are untouched)
rm -rf data/ logs/        # only if you want to wipe trade history and logs
docker compose up --build -d
```

---

## Telegram Alerts Reference

| Alert | When sent |
|---|---|
| 🟡 **Autobot stopped** (graceful shutdown) | Container stopped cleanly (exit code 0) — e.g. after `docker compose stop` |
| 🔴 **Autobot crashed** (exit code: N) | Container exited with non-zero code — will restart automatically |
| ✅/❌ **EXIT / ENTRY** | Bot executed a trade on Kraken |
| ⚠️ **Reconciliation Warnings** | Position mismatch detected at startup |
| 🚨 **Bot Error** | Unhandled exception in the trading loop |
