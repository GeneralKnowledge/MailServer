# Server security standard

One standard for every machine Avedeus runs: the mailcow host, Oracle Always
Free and Hetzner servers, and future GPU or AI compute nodes. Adding a
machine should never mean adding a machine that is weaker than the rest.

- **Apply it:** the Ansible playbook in [`hardening/`](../hardening/README.md).
- **Check it:** [`hardening/audit.sh`](../hardening/audit.sh), read-only, runs on
  any Ubuntu host with or without the playbook (so it also audits machines
  that exist today).
- **Decision:** [ADR 0022](adr/0022-host-hardening-baseline.md).

Status (2026-11-17): **in code, not yet on a real server.** CI applies the
playbook to a fresh Ubuntu VM and checks from outside that only declared
ports answer, including Docker-published ones. Real-server rollout waits with
the rest of the server work.

## Why

A new public IP address is found within minutes, not days. Botnets and
scanning services sweep the whole IPv4 space continuously, cloud provider
ranges first, and try SSH passwords, default credentials and known exploits
against whatever answers. A domain name, an announcement or an interesting
application is not needed. Once a host has a TLS certificate its name is
also public (see [Fact-check](#fact-check)).

So the goal is simple: **nothing answers from the internet unless it is
meant to, and what answers is up to date and needs a key.** Then the
noise is harmless, and the alerts tell you about the few things that are
not noise.

## Layers of exposure

Each layer must hold on its own; the next one is the safety net.

1. **Provider firewall** (Oracle security list / NSG, Hetzner Cloud
   Firewall). Outside the machine, so Docker cannot punch holes in it and a
   host mistake cannot undo it. Open only the profile's public ports below.
2. **Host firewall** (ufw): deny incoming by default; allow the declared
   ports only.
3. **Docker guard**: Docker-published ports bypass ufw (see
   [the gotcha](#the-ufw--docker-gotcha)); the guard filters them in
   Docker's `DOCKER-USER` chain.
4. **Private paths for admin access**: SSH and admin pages over Tailscale
   (the tailnet), web apps that need to be public through a Cloudflare
   Tunnel where they can be. Mail ports cannot use either.
5. **The service itself**: keys only for SSH, 2FA for admin consoles,
   updates applied automatically.

## Controls

| # | Control | What the baseline does | What the audit checks |
|---|---|---|---|
| S1 | Accounts | One admin account with an SSH key; no root login; nothing extra. Give the admin a strong local password kept in your password manager: it is not usable over SSH, only for the provider's web console when you are locked out. | no uid 0 besides root; no empty passwords; lists the `docker` group (root-equivalent) |
| S2 | SSH | Keys only (`AuthenticationMethods publickey`), no root, no passwords or keyboard-interactive, `MaxAuthTries 3`, `LoginGraceTime 30`, no X11/agent forwarding, local forwards only. Written to `sshd_config.d/01-hardening.conf` (sorts first, so cloud-init's `50-` file cannot override it) and verified with `sshd -T`. Refuses to run if the admin has no key. | the effective `sshd -T` values |
| S3 | Firewall | ufw: deny incoming, allow outgoing, log blocked packets. SSH tailnet-only, from listed addresses, or rate-limited (`ufw limit`). Declared host ports only. Docker guard for published ports. Oracle images: its stock iptables rules handed over to ufw (below). | ufw active and default-deny; every rule open to everyone is declared; every Docker-published port is declared or guarded; Docker API not on TCP; listening sockets |
| S4 | Brute-force bans | fail2ban: `sshd` jail (aggressive mode, systemd journal) and `recidive` (a week for repeat offenders); tailnet never banned. mailcow's own netfilter covers mail logins on the mail host. | the `sshd` jail (or CrowdSec) is running |
| S5 | Updates and time | Daily unattended security updates; reboot at 04:30 when a kernel needs it (off for GPU nodes); needrestart restarts services after library updates; chrony; persistent journal (evidence survives a reboot); a short list of kernel network settings. | unattended upgrades on; reboot pending (FAIL after 7 days); security updates waiting; clock synchronised |
| S6 | Private access | Optional Tailscale (OpenSSH stays the SSH server, so keys, alerts and fail2ban still apply; refuses to close public SSH unless Tailscale is connected). Optional Cloudflare Tunnel (`cloudflared` service). | Tailscale connected when SSH is tailnet-only |
| S7 | Alerts and diagnostics | A push (ntfy) on every SSH login, at most one per user and address per 10 minutes. A daily digest: logins, SSH probes and top sources, firewall blocks and most-probed ports, Docker guard blocks, bans, and the audit's warnings; high priority if the audit fails; pings healthchecks.io so a dead host is noticed. Same settings as the mail server's [alerts](setup-guide.md#part-e--backups-and-a-restore-test). | login alert hooked into PAM; push destination set; digest timer on |

Deliberately left out, to keep the baseline small (per the project's
monitoring rule, alerts and diagnostics first): Prometheus/Grafana, CrowdSec
(fail2ban covers SSH; revisit if a web app needs shared blocklists), web
request monitoring or a WAF, auditd and file-integrity tools, SSH 2FA, a
full CIS benchmark, and changing the SSH port (it only reduces log noise; a
tailnet-only SSH removes it entirely).

## Profiles

| Profile | Public (provider firewall + ufw) | SSH | Docker guard | Notes |
|---|---|---|---|---|
| **P1 mail host** (mailcow) | 25, 80, 443, 465, 587, 993, 4190 (all published by mailcow's Docker). 143/110/995 stay on localhost (`harden-mailcow-conf.sh`). | tailnet only | **off for now**, see below; the audit still fails if mailcow publishes anything undeclared | Mail ports cannot go through a tunnel; 80 is needed for ACME. |
| **P2 general server** (Hetzner, Oracle Always Free) | nothing by default; web apps through a Cloudflare Tunnel, or 80/443 if they must be direct | tailnet only | on | Oracle iptables handled automatically. |
| **P3 GPU / AI node** | nothing | tailnet only | on | Model servers (Ollama 11434, vLLM 8000, Jupyter 8888) have no real authentication: tailnet only, never published. No automatic reboot (`hardening_auto_reboot: false`); the digest reports a pending reboot instead. |

Why the guard is off on the mail host for now: every port mailcow publishes
is meant to be public, mailcow manages its own firewall rules (its netfilter
container) and IPv6 handling, and none of this has run on the real server
yet. The guard's design does not touch either (it only returns or drops in
`DOCKER-USER` for packets on the external interface), so turning it on
there is a one-line change once it has been watched next to mailcow on the
box. Until then the audit is the safety net: a published port outside the
list is a FAIL in the next morning's digest.

## The UFW / Docker gotcha

`docker run -p 8080:80` (or `ports:` in compose) is reachable from the
internet **even when ufw says deny**. Docker rewrites the destination of
incoming packets in the `nat` table before filtering (PREROUTING), so they
are forwarded to the container and never pass ufw's INPUT rules. ufw shows
nothing wrong. This is documented by Docker and reproduced in this repo's
CI: with the guard off, an undeclared container port answers from outside
while ufw has no rule for it.

What works, in order of preference:

1. **Don't publish publicly.** Bind to localhost or the tailnet address:
   `-p 127.0.0.1:8080:80` or `-p 100.x.y.z:8080:80`, and reach it through a
   tunnel or the tailnet. Containers on a shared Docker network need no
   published port at all.
2. **Provider firewall**: outside the host, so unaffected.
3. **The guard** (`hardening_docker_guard`): rules in `DOCKER-USER`, the
   chain Docker leaves alone, that drop new connections arriving on the
   external interface unless the *original* destination port (before
   Docker's rewrite, `--ctorigdstport`) is declared. Loaded with ufw (from
   `/etc/ufw/after.rules` and `after6.rules`), so it survives ufw reloads,
   Docker restarts and reboots (all checked in CI).

Not recommended: `"iptables": false` in Docker's `daemon.json` (breaks
container networking in ways that are hard to see) and the `ufw-docker`
script (the guard does the same job in a few rules we own).

## Oracle Cloud images

Oracle's Ubuntu images ship their own firewall: `iptables-persistent`
loads `/etc/iptables/rules.v4` at boot, which rejects everything in INPUT
and FORWARD except SSH from anywhere. It and ufw cannot coexist (installing
ufw removes `iptables-persistent`). The baseline:

1. keeps a copy as `/etc/iptables/rules.v4.pre-hardening`;
2. removes only Oracle's INPUT/FORWARD rules from the running firewall, one
   by one (so Docker's chains are untouched and no reboot is needed);
3. carries Oracle's `InstanceServices` OUTPUT rules over into ufw, so they
   still load at boot. They keep the iSCSI boot volume (169.254.0.2:3260)
   reachable by root only. Oracle warns against removing them.

The OCI security list (or a Network Security Group) is still the first
layer: open only the profile's ports there. Oracle also blocks outbound port
25 for new tenancies until a service request is approved
([m0-readiness-report](m0-readiness-report.md)).

## Auditing a server that exists today

No Ansible needed. On each machine:

```sh
scp hardening/audit.sh host:
ssh host 'sudo PUBLIC_TCP="22 80 443" bash audit.sh'   # list what is meant to be public
```

It prints PASS / WARN / FAIL / INFO per check and exits 1 on any FAIL.
Then check what the script cannot see:

- [ ] Provider firewall: only the profile's ports open; SSH limited to your
      address or closed (tailnet).
- [ ] `~/.ssh/authorized_keys` on each host: only keys you recognise.
- [ ] Cloud console, Cloudflare, Tailscale and GitHub accounts: 2FA on.
      These accounts can reach every machine, so they matter more than any
      one host.
- [ ] Tailscale: key expiry on for servers you don't touch often; ACLs so
      that a GPU node cannot reach the mail host's admin pages.
- [ ] Anything listening that you can't name (the audit lists sockets).
- [ ] Docker containers you no longer use: stop them (and their ports).

Fix the FAILs first. The fastest way to fix most of them is to apply the
playbook.

## Adding a new machine

1. Create it with your SSH key; in the provider firewall open nothing but
   SSH from your address (the playbook will move SSH to the tailnet).
2. Add it to `hardening/inventory.yml` under its profile.
3. `ansible-playbook site.yml --limit newhost --check --diff`, then without
   `--check`.
4. Run `sudo hardening-audit` on it: no FAIL. Close SSH in the provider
   firewall once it is reachable over the tailnet.
5. Confirm the login push arrived and, the next morning, the digest.

## Fact-check

The video transcript was not shared, so these are the claims named in the
summary of it, checked against how the systems behave (the Docker claim is
also reproduced in CI).

| Claim | Verdict |
|---|---|
| A server is attacked almost immediately after it is exposed | **True.** Fresh cloud IPs typically see SSH login attempts within minutes to hours; scanners sweep the whole IPv4 space in under an hour and target cloud ranges. With key-only SSH these attempts are noise, which is why the digest summarises them instead of alerting on each. |
| UFW does not protect Docker-published ports | **True** for ports published with `-p`/`ports:` (above). It does not apply to containers that publish nothing or bind to 127.0.0.1. |
| Certificate Transparency reveals your hosts | **True.** Every publicly trusted certificate is logged in public CT logs within minutes (searchable on crt.sh); scanners watch those logs to find new hosts and attack them while they are being set up. A wildcard certificate hides the individual names but not the domain. For us: `mx.avedeus.ovh` and mailcow's `autodiscover`/`autoconfig` names become public when mailcow requests its certificate. Finish setup (admin password, 2FA) before the certificate is issued. |
| fail2ban protects the server | **Partly.** It slows password guessing and cuts noise; with key-only SSH there is nothing for it to guess. Its default bans go in INPUT, which Docker-published ports bypass, and it only sees the logs it is pointed at. On the mail host mailcow's netfilter does this job for mail logins. |
| Changing the SSH port helps | **Only for log noise.** Scanners find other ports quickly. Closing public SSH (tailnet-only) is the real fix. |
| Cloudflare Tunnel hides the server | **For HTTP(S), mostly.** No inbound port is needed, but the origin IP can still be found if anything else on it is public or old DNS records named it, and Cloudflare terminates TLS (it sees the traffic). It cannot carry SMTP or IMAP. |
| Tailscale makes SSH safe | **It moves the risk** from the internet to your Tailscale account: protect that with 2FA, use ACLs, and keep a way in that doesn't depend on it (the provider's web console with the admin's local password). |
