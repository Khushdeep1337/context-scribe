# Cybera deployment checkpoint

Target: one Ubuntu 24.04 LTS VM on Cybera Rapid Access Cloud, one Discord guild,
one worker, hosted Gemini inference. Prepared locally; not yet deployed to Cybera.
Use a Python virtual environment and systemd, without containers or a public API.

Local checks: 28 tests passed on Windows; 27 passed on Linux with the optional
Discord adapter test skipped. Both offline demos passed. Systemd 259 accepted
the CRLF unit syntax using a validation copy with the local Python executable
substituted for the not-yet-installed VM interpreter. Service startup, mounts,
network access, and reboot recovery still require validation on the actual VM.

Cybera describes RAC as a development/prototyping resource, not permanent
production hosting. Confirm your allocation and VM image availability. Its FAQ
recommends volumes for persistent data and operator-managed backups. See
[RAC](https://www.cybera.ca/rapid-access-cloud/) and the
[official FAQ](https://wiki.cybera.ca/display/RAC/FAQ). The linked older detailed
guides were unavailable during preparation; portal labels and quotas may differ.

## 1. VM and persistent storage

- Use Ubuntu 24.04 with Python 3.12/systemd. Starting sizing estimate: 2 vCPUs,
  4 GiB RAM, 20 GiB boot disk; this is not a measured requirement.
- Add your SSH public key. Allow inbound TCP 22 only from your administrator IP
  or trusted network. No HTTP, database, or bot ports need to be opened.
- Allow outbound DNS and HTTPS/WebSocket access to Discord and your model provider;
  setup also requires package repositories. Discord connections are outbound.
- Use the volume guide linked from Cybera's FAQ to attach a dedicated volume,
  format it only if new/empty, and mount it at `/var/lib/discord-scribe`. Use a
  local Linux filesystem such as ext4 and its UUID in `/etc/fstab`. The service
  refuses startup if that path is not a mount point.
- Connect using the address assigned by Cybera; do not assume a public IPv4
  allocation. The Ubuntu image's default SSH user is typically `ubuntu`.

Commands run on the VM unless labelled PowerShell:

```sh
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ca-certificates
sudo useradd --system --user-group --home-dir /var/lib/discord-scribe --shell /usr/sbin/nologin discord-scribe
mountpoint /var/lib/discord-scribe
```

Reuse the service account if it already exists. Stop if `mountpoint` fails.
After mounting the volume:

```sh
sudo chown discord-scribe:discord-scribe /var/lib/discord-scribe
sudo chmod 0700 /var/lib/discord-scribe
sudo install -d -m 0755 /opt/discord-scribe
sudo install -d -o root -g discord-scribe -m 0750 /etc/discord-scribe
```

## 2. Transfer reviewed source

Review/commit locally first if you want a reproducible checkpoint. These commands
do not stage, commit, or push. From the project folder in Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force data | Out-Null
tar --exclude=__pycache__ -czf data/cybera-source.tar.gz discord_scribe tests deploy docs pyproject.toml README.md LICENSE
scp data/cybera-source.tar.gz ubuntu@YOUR_VM_ADDRESS:cybera-source.tar.gz
```

This archive uses current files, including uncommitted edits. It excludes local
`scribe.toml`, databases, `.env`, `.venv`, `.git`, and generated reports. Inspect its
contents if you have added private files inside the included directories.

On the VM, install your own trusted archive:

```sh
sudo tar --no-same-owner -xzf ~/cybera-source.tar.gz -C /opt/discord-scribe
sudo python3 -m venv /opt/discord-scribe/.venv
sudo /opt/discord-scribe/.venv/bin/python -m pip install '/opt/discord-scribe[all]'
sudo /opt/discord-scribe/.venv/bin/python -m pip check
cd /opt/discord-scribe
.venv/bin/python -m unittest discover -s tests -v
```

The runtime account cannot modify root-owned application code. Direct integration
dependencies are pinned; transitive dependencies resolve at install time, so this
is not a fully locked reproducible build.

## 3. Configuration and credentials

```sh
sudo install -o root -g discord-scribe -m 0640 deploy/cybera/scribe.example.toml /etc/discord-scribe/scribe.toml
sudo install -o root -g root -m 0600 deploy/cybera/credentials.env.example /etc/discord-scribe/credentials.env
sudoedit /etc/discord-scribe/scribe.toml
sudoedit /etc/discord-scribe/credentials.env
```

Set real guild/channel/author IDs and credentials. Preserve the two
`/var/lib/discord-scribe` paths. Keep credentials outside the repository.

Stop the laptop bot before starting the hosted copy. Worker locks protect one
database on one machine, not two independent copies of the same bot. For existing
state, stop the local bot and CLI writers, create a consistent SQLite backup,
transfer it privately, and install it as `/var/lib/discord-scribe/scribe.sqlite3`,
owned by `discord-scribe`, mode 0600. Preserve the same `project` and `guild_id`:
the DB rejects a different identity. Otherwise start with a fresh empty queue.

## 4. Verify and start

Repository files retain CRLF. There is no Linux shell entrypoint/shebang requiring
conversion; validate the unit with the actual VM's systemd before enabling it.

```sh
sudo install -m 0644 deploy/cybera/discord-scribe.service /etc/systemd/system/discord-scribe.service
sudo systemd-analyze verify /etc/systemd/system/discord-scribe.service
sudo systemctl daemon-reload
sudo systemctl enable --now discord-scribe
sudo systemctl status discord-scribe --no-pager
sudo journalctl -u discord-scribe -n 30 --no-pager
```

Expect `Discord connected; configured capture scope is active`. First startup
schedules extraction an hour later. Use `interval_seconds = 60` before first
startup for a quicker test. The deadline persists, so changing the interval does
not reset an existing deadline. Send synthetic messages, then inspect:

```sh
sudo -u discord-scribe /opt/discord-scribe/.venv/bin/python -m discord_scribe --config /etc/discord-scribe/scribe.toml status
sudo -u discord-scribe cat /var/lib/discord-scribe/discord-context.md
```

The journal reports attempted batch counts and safe error classes. Process
liveness is not extraction health: inspect failures and the actual output. During
a maintenance window, reboot and verify the mount, service, queue, and export.
Systemd restarts unexpected exits, but does not detect every hung worker.

## 5. Download context into your codebase

The VM cannot directly write to your laptop. For this checkpoint, retrieve an
SSH snapshot; automatic sync and scheduled Discord attachments are not built.
On the VM, create a private copy for your SSH account:

```sh
cd ~
sudo install -o "$(id -un)" -g "$(id -gn)" -m 0600 /var/lib/discord-scribe/discord-context.md ./discord-context.download.md
```

From the target codebase on your laptop:

```powershell
New-Item -ItemType Directory -Force .context | Out-Null
scp ubuntu@YOUR_VM_ADDRESS:discord-context.download.md .context/discord-context.md
```

Ignore `.context/` if the content is private. Ask the coding agent to read it as
evidence, not authority. Downloaded snapshots do not update or delete themselves.

## Operations and recovery

```sh
sudo systemctl stop discord-scribe
sudo systemctl restart discord-scribe
sudo journalctl -u discord-scribe --since '1 hour ago' --no-pager
```

- Restart after changing config or credentials. After five rapid failures, fix
  the cause, run `sudo systemctl reset-failed discord-scribe`, then start it.
- Manual `process` needs model credentials in its own process environment and
  cannot run alongside the service worker. `status`, `reject`, and `export` do not
  require model credentials.
- Keep the previous source archive for rollback. Stop the service, install updates
  into a fresh code directory/venv, validate, then update the unit's code paths and
  restart. Never remove the data volume during an update.
- No offline history reconciliation: messages/deletions missed during downtime
  may leave missing/stale context. One VM remains a single point of failure.

Create consistent backups using SQLite's backup API, not a copy of only the main
file while WAL writers are active. On the VM:

```sh
sudo -u discord-scribe /opt/discord-scribe/.venv/bin/python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

folder = Path('/var/lib/discord-scribe/backups')
folder.mkdir(mode=0o700, exist_ok=True)
name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
destination = folder / f'{name}.sqlite3'
source = sqlite3.connect('file:/var/lib/discord-scribe/scribe.sqlite3?mode=ro', uri=True)
target = sqlite3.connect(destination)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
destination.chmod(0o600)
print(destination)
PY
```

Keep protected copies off the VM/volume too. Test restoration into a separate DB.
Restore with the service and all DB users stopped, preserving the old DB and its
WAL/SHM files together before substituting a backup. Choose backup retention:
backups can retain deleted messages. No automatic backup job is installed.

References: [service options](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[execution isolation](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html),
[shutdown signals](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html).
