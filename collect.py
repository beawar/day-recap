#!/usr/bin/env python3
"""Collect today's 08:30-16:00 activity (Claude sessions + GitHub) as compact JSON for /day-recap."""
import glob, json, os, re, subprocess, sys
from datetime import datetime, time

IDLE_GAP_MIN = 15
JIRA = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
PR_DIR = re.compile(r"-pr-(\d+)$")
HOME_PREFIX = os.path.expanduser("~").replace("/", "-") + "-"

now = datetime.now().astimezone()
day = now.date()
if len(sys.argv) > 1:  # optional YYYY-MM-DD for testing past days
    day = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
start = datetime.combine(day, time(8, 30)).astimezone()
end = datetime.combine(day, time(16, 0)).astimezone()

if day.weekday() >= 5:
    print(json.dumps({"skip": True, "reason": "weekend"}))
    sys.exit()


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()


def jira_key(*texts):
    for t in texts:
        m = JIRA.search(t or "")
        if m:
            return m.group(1)
    return None


def active_minutes(stamps):
    stamps = sorted(stamps)
    secs = sum(
        (b - a).total_seconds()
        for a, b in zip(stamps, stamps[1:])
        if (b - a).total_seconds() <= IDLE_GAP_MIN * 60
    )
    return round(secs / 60)


def blocks(stamps):
    """Split sorted stamps into active runs at idle gaps."""
    runs = [[stamps[0], stamps[0]]]
    for t in stamps[1:]:
        if (t - runs[-1][1]).total_seconds() > IDLE_GAP_MIN * 60:
            runs.append([t, t])
        else:
            runs[-1][1] = t
    return runs


def timeline(raw):
    """Merge same-key blocks that overlap or sit within the idle gap; drop blips under 5 min."""
    merged = []
    for key in {r[0] for r in raw}:
        runs = sorted(([a, b, {title} - {None}, ps] for k, a, b, title, ps in raw if k == key), key=lambda r: r[0])
        out = [runs[0]]
        for a, b, titles, ps in runs[1:]:
            if (a - out[-1][1]).total_seconds() <= IDLE_GAP_MIN * 60:
                out[-1][1] = max(out[-1][1], b)
                out[-1][2] |= titles
                out[-1][3] += ps
            else:
                out.append([a, b, titles, ps])
        merged += [(a, b, key, sorted(t), ps) for a, b, t, ps in out if (b - a).total_seconds() >= 300]
    return [{"from": a.strftime("%H:%M"), "to": b.strftime("%H:%M"), "key": k, "titles": t, "prompts": ps[:2] + ps[2:][-1:]}
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
        pr = PR_DIR.search(folder)
        key = jira_key(branch, folder) or (f"PR#{pr.group(1)}" if pr else folder.replace(HOME_PREFIX, "") or folder)
        it = items.setdefault(key, {"key": key, "minutes": 0, "titles": [], "branches": set(), "prompts": [], "span": [stamps[0], stamps[-1]]})
        it["minutes"] += active_minutes(stamps)
        stamps.sort()
        raw += [(key, a, b, title, [p for t, p in prompts if a <= t <= b]) for a, b in blocks(stamps)]
        if title:
            it["titles"].append(title)
        if branch and branch != "HEAD":
            it["branches"].add(branch)
        it["prompts"] += [p for _, p in prompts[:3] + prompts[3:][-2:]]
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
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    return json.loads(r.stdout or "[]") if r.returncode == 0 else []


def prs():
    fields = "number,title,repository,state,url,updatedAt"
    common = ["search", "prs", "--owner", "unobravo", "--updated", f">={day}", "--json", fields, "--limit", "50"]
    mine = gh(*common, "--author", "@me")
    reviewed = gh(*common, "--reviewed-by", "@me")
    me = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True).stdout.strip()

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
print(json.dumps({"date": str(day), "window": "08:30-16:00", "claude": claude, "claude_timeline": claude_timeline,
                  "authored_prs": authored, "reviews": reviews}, ensure_ascii=False, default=str))
