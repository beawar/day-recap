# day-recap

Claude Code skill: recaps your 08:30–16:00 workday (Claude sessions, meetings, GitHub PRs/reviews, Jira) and DMs it to you on Slack.

Needs `gh` logged in, plus the Google Calendar, Atlassian and Slack connectors in Claude.

## Install

```sh
git clone https://github.com/beawar/day-recap ~/.claude/skills/day-recap
mkdir -p ~/recaps
```

Then run `/day-recap` in Claude Code.

## Output

Each run writes `~/recaps/<YYYY-MM-DD>.md` and sends the same text to you as a Slack DM. See [examples/2026-10-26.md](examples/2026-10-26.md) for a sample (fictional data).

Weekends, days off and empty days produce nothing.

## Run it automatically at 16:00 on weekdays (macOS)

```sh
sed "s#__HOME__#$HOME#g" ~/.claude/skills/day-recap/day-recap.plist.template > ~/Library/LaunchAgents/local.day-recap.plist
launchctl load ~/Library/LaunchAgents/local.day-recap.plist
```

The template expects `claude` at `~/.local/bin/claude`. If yours is elsewhere, fix the path in the plist. Logs go to `~/recaps/.log`.
