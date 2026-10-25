# Transactional email

The site sends mail through `app/lib/mailer.js`: SMTP only, no provider SDK.
Sign-up still succeeds when mail is not configured. The process logs one
warning line and does not log the message body or the password.

`GET /api/version` includes `mail_configured` (true when `SMTP_HOST` is set).

The primary path is a Gmail account the owner already has. An own-domain
sender is Option B, further down.

## Option A — Gmail

Gmail speaks SMTP. The mailer uses it directly.

1. On the Google account that should send, turn on 2-Step Verification
   (https://myaccount.google.com/signinoptions/two-step-verification).
2. Create an app password at https://myaccount.google.com/apppasswords.
   Google shows 16 characters, often in groups of four. The value you set is
   those 16 characters with the spaces removed. The account's normal password
   will not authenticate.
3. Put the variables in the website env on the host
   (`~/runeclaw-persist/web.env`), not in the bot's `.env`, and not in git.

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURE=false
SMTP_USER=owner@gmail.com
SMTP_PASS=<16-char app password, set only in the host env, never in git or chat>
PUBLIC_ORIGIN=https://www.humanoid-traders.com
```

`SMTP_SECURE=false` on port 587 is STARTTLS. The mailer requires the relay to
offer it and upgrades before it sends the password. It does not fall through
to a clear connection.

Port 465 is the other Gmail door: `SMTP_PORT=465` and `SMTP_SECURE=true`
(implicit TLS from the first byte).

Leave `MAIL_FROM` unset. The From address is then
`Humanoid Traders <SMTP_USER>`. Gmail rejects or rewrites a From that is not
that account, so the default is the account itself. To set it explicitly:

```
MAIL_FROM=Humanoid Traders <owner@gmail.com>
```

Use the same address as `SMTP_USER`. Replies land in that Gmail inbox, so
`MAIL_REPLY_TO` is optional.

A consumer Gmail account sends about 500 messages a day. That is enough for
sign-up, verification, and password reset. It is not a broadcast channel.

`SMTP_PASS` is read from the environment only. The process never writes it
to the log. A relay that quotes it back still reaches the caller with the
password removed.

## What the messages are

Verification links expire after 24 hours. Password-reset links expire after
30 minutes. Resend is limited to five requests per account per 15 minutes.

A missing `PUBLIC_ORIGIN` / `APP_BASE_URL` / `WEBSITE_URL` while `SMTP_HOST` is
set is fatal in production: the verify link would have no host.

## Option B — your own domain, later

When you want `noreply@humanoid-traders.com` (or another address on the
domain), set `MAIL_FROM` to that address and point SMTP at a provider that
is allowed to send as it. The mailer does not default to that address.

Publish what the provider gives you. Do not invent a DKIM key.

1. **SPF.** One TXT record on the domain, including that provider's include
   (for Google Workspace, `include:_spf.google.com`). A domain can have only
   one SPF record; add the new include to the existing one.
2. **DKIM.** The provider generates the selector and the TXT value. Add that
   record as they print it. This repository does not contain a key.
3. **DMARC.** Start with monitoring, then tighten after mail is arriving:

   `v=DMARC1; p=none; rua=mailto:info@humanoid-traders.com`

   Move `p=` to `quarantine` or `reject` only after the reports show the
   legitimate mail is aligned.

`MAIL_FROM` must be an address that account may send as. A mismatch is mail
that never arrives.

## Test send

With the website process able to read the variables:

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
error names the failure class and does not print `SMTP_PASS`.
