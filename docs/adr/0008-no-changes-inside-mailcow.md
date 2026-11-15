# 0008. No changes inside the mailcow source tree

- Status: Accepted (2026-11-15). Locked constraint 4.
- Implemented: Yes. We use only `docker-compose.override.yml`,
  `data/conf/nginx/site.*.custom`, SOGo custom theme files and the custom
  mailcow CSS, all of which mailcow's `.gitignore` excludes. Proven on Oracle:
  No (verified with `docker compose config` and the 14 Nov x86 rehearsal).

## Decision

- mailcow is installed from upstream release tags and updated with its own
  `update.sh`. We change it only through its supported override and custom
  files, kept in `infrastructure/mailcow/configuration/` and copied in by
  scripts.
- Nothing may block a mailcow update. If a feature needs a change mailcow
  does not support, we do not build that feature that way.

## Consequences

- Some branding must be re-applied after each update (`apply-branding.sh`).
- Extensions that run inside mailcow (for example MediaLab Sign-up) are
  rejected ([0011](0011-rejected-projects.md)).
