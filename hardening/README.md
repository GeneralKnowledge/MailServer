# Host hardening baseline

Ansible playbook that applies the [server security standard](../docs/server-security-standard.md)
to every Ubuntu machine (22.04/24.04): the mailcow host, Oracle and Hetzner
servers, GPU nodes. Safe to re-run; a second run changes nothing.

| Path | What |
|---|---|
| `site.yml` | the playbook (one role, `hardening`) |
| `roles/hardening/defaults/main.yml` | every setting, documented |
| `inventory.example.yml` | the three machine profiles; copy to `inventory.yml` (gitignored) |
| `audit.sh` | read-only check of one host against the standard; no Ansible needed |
| `ci/runner-test.sh` | the CI proof (applies the playbook to a throwaway VM and tests from outside) |

## Use

From your own computer (Ansible 2.16+):

```sh
cd hardening
pip install ansible-core && ansible-galaxy collection install -r requirements.yml
cp inventory.example.yml inventory.yml            # fill in hosts
ansible-vault create group_vars/all/vault.yml     # vault_ntfy_url, vault_tailscale_authkey, ...
ansible-playbook site.yml --check --diff --ask-vault-pass   # what would change
ansible-playbook site.yml --ask-vault-pass
```

On each host afterwards: `sudo hardening-audit` (no FAIL), and
`sudo hardening-digest` sends today's digest now.

Lock-out guards: the run stops before turning off SSH passwords if the
admin account has no `authorized_keys`, and before closing public SSH if
Tailscale is not connected. Keep a second SSH session open the first time
anyway, and know how to reach the provider's web console.

## What lands on a host

| Path | Purpose |
|---|---|
| `/etc/ssh/sshd_config.d/01-hardening.conf` | SSH settings |
| `/etc/ufw/after.rules`, `after6.rules` (marked blocks) | Docker guard; Oracle's 169.254.0.0/16 rules |
| `/etc/fail2ban/jail.d/hardening.local` | sshd and recidive jails |
| `/etc/apt/apt.conf.d/99hardening-auto-upgrades`, `52hardening-unattended` | updates and reboot window |
| `/etc/sysctl.d/60-hardening.conf` | kernel network settings |
| `/etc/hardening/policy.env` | what the host may expose (read by the audit) |
| `/etc/hardening/alerts.env` (600) | ntfy / healthchecks settings |
| `/usr/local/sbin/hardening-{audit,digest,login-alert}` | the scripts |
| `hardening-digest.timer` | daily digest |
| `/etc/pam.d/sshd` (one line) | login alert |

## Docker hosts

Publish container ports to `127.0.0.1` or the tailnet unless they are meant
to be public. Ports that are meant to be public go in
`hardening_docker_public_tcp_ports`; with `hardening_docker_guard: true`
(the default) every other published port is dropped on the external
interface. See [the UFW / Docker gotcha](../docs/server-security-standard.md#the-ufw--docker-gotcha).
