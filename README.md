# day-recap

Claude Code skill: recaps your workday (Claude sessions, meetings, GitHub PRs/reviews, Jira) and DMs it to you on Slack.

Needs `gh` logged in, plus the Google Calendar and Slack connectors in Claude. The Atlassian connector is optional, for Jira.

## Install

```sh
git clone https://github.com/beawar/day-recap ~/.claude/skills/day-recap
```

Then run `/day-recap` in Claude Code. The first run asks a few questions and writes your config; `/day-recap setup` changes it later.

## Config

Lives at `~/.config/day-recap/config.json` (or `$XDG_CONFIG_HOME/day-recap/config.json`), outside the skill folder so `git pull` never touches it. See [examples/config.json](examples/config.json).

| Field | Default | Meaning |
|---|---|---|
| `github_orgs` | `[]` | Orgs whose PRs to include. Empty means all. |
| `jira_site` | `null` | e.g. `acme.atlassian.net`. `null` skips Jira. |
| `workday` | 08:30–16:00, Mon–Fri | `start`, `end`, and `days` as ISO weekday numbers (1 = Monday). |
| `recaps_dir` | `~/recaps` | Where recaps and the scheduler log go. |
| `claude_bin` | `claude` on your PATH | Used only by the scheduled job. |

## Output

Each run writes `<recaps_dir>/<YYYY-MM-DD>.md` and sends the same text to you as a Slack DM. See [examples/2026-10-26.md](examples/2026-10-26.md) for a sample (fictional data).

Non-workdays, days off and empty days produce nothing.

## Run it automatically at the end of each workday (macOS)

```sh
python3 ~/.claude/skills/day-recap/collect.py --plist > ~/Library/LaunchAgents/local.day-recap.plist
launchctl load ~/Library/LaunchAgents/local.day-recap.plist
```

The job is built from your config, so rerun both commands (with `launchctl unload` first) after changing `workday`, `recaps_dir` or `claude_bin`. Logs go to `<recaps_dir>/.log`.
