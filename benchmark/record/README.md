# The weekly public record

One file a week, `<YYYY>-W<ww>.json` (ISO week), written on the bot's server by

    python3 scripts/weekly_record.py
    node app/scripts/sync_content.js

and committed as it was written. The website's `/record` page serves these
files (its copy is `app/content/record/`), and `python3 scripts/verify_evidence.py`
checks each one: its format, and that no dollar figure is on it.

What a week holds is `bot/core/public_record.py`, in percent, ratio and count
only:

- **sleeve**: that the daily trend sleeve's paper book is being kept: days
  recorded, the first and last day, gaps. Nothing that is in a row (no
  weights, holdings or returns): the sleeve's pre-registration
  (`docs/research/daily_trend_sleeve_prereg.md`) seals its first measurement
  for 2027-10-17, by someone who has not looked at the rows before then.
- **equity**: the live book's day-over-day returns, maximum drawdown and,
  once 30 daily returns exist, a Sharpe ratio. Deposits and withdrawals are
  not separated from trading in the log, and every week says so.
- **parity**: live exits against the backtest, as the dashboard's reports panel
  already serves them.

A section that could not be read says so (`"state": "unreadable"` and the
exception class); a source with no rows is `"absent"`. Neither is a zero.

A committed week is not rewritten. The script refuses to replace one without
`--force`, and a correction belongs in the next week's file.
