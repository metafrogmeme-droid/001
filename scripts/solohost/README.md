# RUNECLAW Local: the SoloHost package

SoloHost is the app framework inside Pi Desktop: it installs and runs
self-hosted apps on a Pi Node operator's own computer. These two files are the
whole package:

| File | What it is |
| --- | --- |
| `docker-compose.yml` | Runs the bot from the release image, with its dashboard on `127.0.0.1:18480` |
| `config_options.yml` | The install form: Telegram bot token, the owner's Telegram ID, a dashboard token, an optional AI key |

The install is **practice only**. `SIMULATION_MODE`, `LIVE_TRADING_ENABLED`,
`AUTO_CONFIRM_LIVE_ENABLED` and `PAPER_AUTO_ACCEPT` are fixed values the form
never shows, and no exchange key is asked for.
`tests/test_the_solohost_package_runs_what_it_says.py` holds both files to
SoloHost's rules (SOLOHOST.md in github.com/pi-node/solohost) and to the
environment variables the bot actually reads.

## Publishing a version

1. **Publish the image.** The compose file pins `v1.0.1`. Push that tag, or
   run the `image` workflow by hand with it. `.github/workflows/image.yml`
   builds the Dockerfile's production stage and pushes
   `ghcr.io/metafrogmeme-droid/runeclaw:<tag>`. It refuses a tag that is
   already published, so `v1.0.0` cannot be rebuilt in place. Until `v1.0.1`
   is pushed, the pin does not exist on ghcr.io.
2. **Make the package public, once.** On GitHub: the repository, Packages,
   `runeclaw`, Package settings, Change visibility to Public. SoloHost pulls
   without credentials.
3. **Pin the tag.** Set `image:` in `docker-compose.yml` to the tag from step 1
   if it differs, and commit.
4. **Validate.** From a checkout of github.com/pi-node/solohost:
   `python3 scripts/validate_api.py <path to this folder>`. It must end in
   `Result: OK`.
5. **Publish in SoloHost.** In Pi Desktop, open SoloHost, start a new app,
   paste the contents of `docker-compose.yml` and `config_options.yml` into
   their fields, and submit.

## What an operator does after installing

1. Create a bot with @BotFather and get their numeric ID from @userinfobot.
2. Fill the form and install.
3. Open the bot in Telegram, send `/start`, then `/paper on`.
4. Open the App tab and enter the dashboard token.

A token Telegram rejects stops the bot at boot with one line,
`Telegram rejected TELEGRAM_BOT_TOKEN (InvalidToken)`. The token itself is not
printed.
