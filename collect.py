#!/usr/bin/env python3
"""Collect today's workday activity (Claude sessions + GitHub) as compact JSON for /day-recap."""
import glob, json, os, plistlib, re, shutil, subprocess, sys
from datetime import datetime, time, timedelta

GAP = timedelta(minutes=15)  # idle gap that splits active time
JIRA = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
PR_DIR = re.compile(r"-pr-(\d+)$")
HOME_PREFIX = os.path.expanduser("~").replace("/", "-") + "-"
CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "day-recap", "config.json")
DEFAULTS = {"github_orgs": [], "jira_site": None, "workday": {"start": "08:30", "end": "16:00", "days": [1, 2, 3, 4, 5]},
            "recaps_dir": "~/recaps", "claude_bin": shutil.which("claude") or "~/.local/bin/claude"}

if not os.path.exists(CONFIG):
    print(json.dumps({"setup_needed": True, "config_path": CONFIG, "defaults": DEFAULTS}))
    sys.exit()
with open(CONFIG) as f:
    cfg = {**DEFAULTS, **json.load(f)}
cfg["workday"] = {**DEFAULTS["workday"], **cfg["workday"]}
cfg["recaps_dir"] = os.path.expanduser(cfg["recaps_dir"])
cfg["claude_bin"] = os.path.expanduser(cfg["claude_bin"])
work = cfg["workday"]
w_start, w_end = (time.fromisoformat(work[k]) for k in ("start", "end"))


def plist():
    """launchd job that runs the recap at the end of each workday."""
    here = os.path.abspath(__file__)
    tools = [f"Bash(python3 {here}*)", f"Edit(/{cfg['recaps_dir']}/**)", "mcp__claude_ai_Google_Calendar__list_events",
             "mcp__claude_ai_Slack__slack_send_message"] + ["mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql"] * bool(cfg["jira_site"])
    path = [os.path.dirname(cfg["claude_bin"])] + [os.path.dirname(b) for b in [shutil.which("gh")] if b] + ["/usr/bin", "/bin"]
    log = os.path.join(cfg["recaps_dir"], ".log")
    return plistlib.dumps({
        "Label": "local.day-recap",
        "ProgramArguments": [cfg["claude_bin"], "-p", "/day-recap", "--model", "sonnet", "--allowedTools", " ".join(tools)],
        "WorkingDirectory": cfg["recaps_dir"],
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path))},
        "StartCalendarInterval": [{"Weekday": d, "Hour": w_end.hour, "Minute": w_end.minute} for d in work["days"]],
        "StandardOutPath": log, "StandardErrorPath": log,
    }).decode()


if "--plist" in sys.argv:
    print(plist(), end="")
    sys.exit()

day = datetime.now().date()
if len(sys.argv) > 1:  # optional YYYY-MM-DD for testing past days
    day = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
start = datetime.combine(day, w_start).astimezone()
end = datetime.combine(day, w_end).astimezone()
errors = []

if day.isoweekday() not in work["days"]:
    print(json.dumps({"skip": True, "reason": "not a workday"}))
    sys.exit()


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()


def jira_key(*texts):
    for t in texts:
        m = JIRA.search(t or "")
        if m:
            return m.group(1)
    return None


def sample(xs, head, tail):
    """The first `head` items plus the last `tail` of the rest."""
    return xs[:head] + xs[head:][-tail:]


def blocks(stamps):
    """Split sorted stamps into active runs at idle gaps."""
    runs = [[stamps[0], stamps[0]]]
    for t in stamps[1:]:
        if t - runs[-1][1] > GAP:
            runs.append([t, t])
        else:
            runs[-1][1] = t
    return runs


def timeline(raw):
    """Merge same-key blocks that overlap or sit within the idle gap; drop blips under 5 min."""
    merged = []
    for key in sorted({r[0] for r in raw}):
        out = []
        for _, a, b, title, ps in sorted((r for r in raw if r[0] == key), key=lambda r: r[1]):
            titles = {title} - {None}
            if out and a - out[-1][1] <= GAP:
                out[-1][1] = max(out[-1][1], b)
                out[-1][2] |= titles
                out[-1][3] += ps
            else:
                out.append([a, b, titles, list(ps)])
        merged += [(a, b, key, sorted(t), ps) for a, b, t, ps in out if b - a >= timedelta(minutes=5)]
    return [{"from": a.strftime("%H:%M"), "to": b.strftime("%H:%M"), "key": k, "titles": t, "prompts": sample(ps, 2, 1)}
            for a, b, k, t, ps in sorted(merged, key=lambda m: m[0])]


def sessions():
    items, raw = {}, []
    for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        if datetime.fromtimestamp(os.path.getmtime(path)).astimezone() < start:
            continue
        folder = os.path.basename(os.path.dirname(path))
        stamps, prompts, title, branch = [], [], None, None
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "ai-title":
                    title = d.get("aiTitle")
                if d.get("type") not in ("user", "assistant") or "timestamp" not in d:
                    continue
                t = ts(d["timestamp"])
                if not start <= t <= end:
                    continue
                stamps.append(t)
                branch = d.get("gitBranch") or branch
                c = d.get("message", {}).get("content")
                if isinstance(c, list):  # skip tool results, keep typed text
                    c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                if d["type"] == "user" and c and not c.startswith("<") and not d.get("isMeta"):
                    prompts.append((t, " ".join(c.split())[:200]))
        if not stamps or (len(stamps) < 2 and not prompts):
            continue
        stamps.sort()
        runs = blocks(stamps)
        pr = PR_DIR.search(folder)
        key = jira_key(branch, folder) or (f"PR#{pr.group(1)}" if pr else folder.removeprefix(HOME_PREFIX) or folder)
        it = items.setdefault(key, {"key": key, "minutes": 0, "titles": [], "branches": set(), "prompts": [], "span": [stamps[0], stamps[-1]]})
        it["minutes"] += round(sum((b - a).total_seconds() for a, b in runs) / 60)
        raw += [(key, a, b, title, [p for t, p in prompts if a <= t <= b]) for a, b in runs]
        if title:
            it["titles"].append(title)
        if branch and branch != "HEAD":
            it["branches"].add(branch)
        it["prompts"] += [p for _, p in sample(prompts, 3, 2)]
        it["span"] = [min(it["span"][0], stamps[0]), max(it["span"][1], stamps[-1])]
    out = []
    for it in sorted(items.values(), key=lambda i: -i["minutes"]):
        if not it["minutes"] and not it["prompts"]:
            continue
        it["branches"] = sorted(it["branches"])
        it["span"] = [s.strftime("%H:%M") for s in it["span"]]
        it["prompts"] = it["prompts"][:6]
        out.append(it)
    keep = {it["key"] for it in out}
    return out, timeline([r for r in raw if r[0] in keep])


def gh(*args):
    """Run gh and parse its JSON; record failures so they don't pass for an empty day."""
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode:
        errors.append(f"gh {' '.join(args[:2])}: {r.stderr.strip()[:200]}")
        return []
    return json.loads(r.stdout or "[]")


def prs():
    fields = "number,title,repository,state,url,updatedAt"
    owners = [a for o in cfg["github_orgs"] for a in ("--owner", o)]
    common = ["search", "prs", *owners, "--updated", f">={day}", "--json", fields, "--limit", "50"]
    mine = gh(*common, "--author", "@me")
    reviewed = gh(*common, "--reviewed-by", "@me")
    me = (gh("api", "user") or {}).get("login")

    def slim(p):
        return {"pr": f"{p['repository']['name']}#{p['number']}", "title": p["title"], "state": p["state"],
                "url": p["url"], "jira": jira_key(p["title"])}

    reviews = []
    own = {p["url"] for p in mine}
    for p in (p for p in reviewed if p["url"] not in own):
        rs = gh("api", f"repos/{p['repository']['nameWithOwner']}/pulls/{p['number']}/reviews")
        states = [r["state"] for r in rs
                  if r.get("user", {}).get("login") == me and r.get("submitted_at") and start <= ts(r["submitted_at"]) <= end]
        if states:
            reviews.append({**slim(p), "review": states[-1]})
    return [slim(p) for p in mine], reviews


authored, reviews = prs()
claude, claude_timeline = sessions()
print(json.dumps({"date": str(day), "window": f"{work['start']}-{work['end']}",
                  "config": {k: cfg[k] for k in ("jira_site", "recaps_dir")}, "claude": claude, "claude_timeline": claude_timeline,
                  "authored_prs": authored, "reviews": reviews, "errors": errors}, ensure_ascii=False))
