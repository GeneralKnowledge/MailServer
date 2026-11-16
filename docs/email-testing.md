# Email testing checklist (M2)

> Daily, `scripts/delivery-check.sh` (PR 30) repeats the parts a script can
> see: DNS, blocklists, and a probe to an outside mailbox that passes when the
> receiving server accepts it. Inbox versus spam, and how a message looks,
> stay manual checks from this list.

Nothing counts as working until it has been tested for real and recorded here.

| Test | How | Result / date |
|---|---|---|
| Outbound port 25 | `scripts/m0-inspect.sh` banner check | |
| Internal delivery | mailbox A → mailbox B in SOGo | |
| Receive from Gmail | send from a Gmail account | |
| Receive from Outlook.com | send from Outlook.com | |
| Send to Gmail | check inbox vs spam; "Show original": SPF, DKIM, DMARC = PASS | |
| Send to Outlook.com | check inbox vs junk; message headers `Authentication-Results` | |
| Third provider | e.g. Proton or Fastmail | |
| Score | send to the address given by https://www.mail-tester.com, aim ≥ 9/10 | |
| TLS | `openssl s_client -connect mail.example.com:465` and `-starttls smtp -connect …:587`; inbound TLS shown in Gmail headers | |
| Reverse DNS | `dig -x <IP>` = `mail.example.com`, and `dig mail.example.com` = IP | |
| IMAP | Thunderbird or iOS Mail with autoconfig | |
| SMTP auth | send from the mail app via 465 and 587 | |
| SOGo | login, read, compose, reply, forward, folders, search, attachments, contacts, phone browser | |
| Registration | full invite flow, then log in to SOGo immediately | |
