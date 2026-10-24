# Transactional email

The site sends mail through `app/lib/mailer.js`: SMTP only, no provider SDK.
Sign-up still succeeds when mail is not configured. The process logs one
warning line and does not log the message body.

`GET /api/version` includes `mail_configured` (true when `SMTP_HOST` is set).

## Environment

Set these in the website env (`~/runeclaw-persist/web.env` on the deploy
host), not in the bot's `.env`.

| Variable | Required | Meaning |
|---|---|---|
| `SMTP_HOST` | yes, to send | Relay hostname |
| `SMTP_PORT` | no | `587` (STARTTLS) or `465` (implicit TLS). Default `587`. |
| `SMTP_SECURE` | no | `true` for implicit TLS on port 465. Otherwise STARTTLS. |
| `SMTP_USER` | usually | Login name. Omit for an open relay. |
| `SMTP_PASS` | usually | Password or the provider's SMTP API key. |
| `MAIL_FROM` | no | Sender override. Blank falls through to `SMTP_FROM`, then to `Humanoid Traders <noreply@humanoid-traders.com>`. |
| `SMTP_FROM` | no | Same job as `MAIL_FROM`, used only when `MAIL_FROM` is blank. |
| `MAIL_REPLY_TO` | no | Where a reply should go when the sender is not read. |
| `PUBLIC_ORIGIN` | yes, once mail sends | Public site URL used in links. `APP_BASE_URL`, then `WEBSITE_URL`, are the fallbacks (`app/lib/public_origin.js`). |

Example, placeholders only:

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURE=false
SMTP_USER=apikey
SMTP_PASS=replace-me
MAIL_REPLY_TO=info@humanoid-traders.com
PUBLIC_ORIGIN=https://www.humanoid-traders.com
```

Leave `MAIL_FROM` unset to use `Humanoid Traders <noreply@humanoid-traders.com>`.
Set it when the provider only allows a specific mailbox.

A missing `PUBLIC_ORIGIN` / `APP_BASE_URL` / `WEBSITE_URL` while `SMTP_HOST` is
set is fatal in production: the verify link would have no host.

Verification links expire after 24 hours. Password-reset links expire after
30 minutes. Resend is limited to five requests per account per 15 minutes.

## DNS on humanoid-traders.com

Publish what the provider you picked gives you. Do not invent a DKIM key.

1. **SPF.** One TXT record on the domain, including that provider's include
   (for example `include:_spf.google.com` for Google Workspace, or the
   include the provider's dashboard prints). A domain can have only one SPF
   record; add the new include to the existing one.
2. **DKIM.** The provider generates the selector and the TXT value. Add that
   record as they print it. This repository does not contain a key.
3. **DMARC.** Start with monitoring, then tighten after mail is arriving:

   `v=DMARC1; p=none; rua=mailto:info@humanoid-traders.com`

   Move `p=` to `quarantine` or `reject` only after the reports show the
   legitimate mail is aligned.

## Test send

With the website process running and the variables set:

```
node -e "
const mailer = require('./app/lib/mailer');
mailer.sendMail({
  to: 'you@example.com',
  subject: 'Humanoid Traders mail test',
  text: 'If you can read this, SMTP is delivering.',
}).then((r) => console.log(r)).catch((e) => console.error(e && e.name));
"
```

Replace the recipient. A skipped result means `SMTP_HOST` is unset. A thrown
error names the SMTP failure and does not print `SMTP_PASS`.
