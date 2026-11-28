"""Posts every due item in schedule.csv to Instagram (single images, carousels, reels).

Runs inside GitHub Actions. Needs two repository secrets:
  IG_USER_ID    - your Instagram professional account ID
  IG_TOKEN      - your Instagram access token
Images are served from this public repo; videos via the jsDelivr CDN mirror of it.

schedule.csv columns: n,date,time,type,image,design,author,source,quote,caption,posted
  type  = IMAGE (default) | CAROUSEL (image = a.jpg|b.jpg|...) | REELS (image = clip.mp4)
"""
import csv, datetime, json, os, sys, time, urllib.parse, urllib.request

HOST = os.environ.get("IG_HOST", "https://graph.facebook.com/v21.0")
USER = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
DRY = os.environ.get("DRY_RUN") == "1"
MAX_PER_RUN = 1


def call(method, path, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{HOST}/{path}"
    req = (urllib.request.Request(url + "?" + data.decode()) if method == "GET"
           else urllib.request.Request(url, data=data, method="POST"))
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Instagram API error {e.code}: {e.read().decode()}")


def img_url(name):
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/posts/{name}"


def vid_url(name):
    return f"https://cdn.jsdelivr.net/gh/{REPO}@{BRANCH}/posts/{name}"


def wait_ready(cid, tries=40, every=8):
    for _ in range(tries):
        st = call("GET", cid, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st == "ERROR":
            raise RuntimeError(f"Instagram could not process container {cid}")
        time.sleep(every)
    raise RuntimeError(f"Container {cid} not ready in time")


def publish(row):
    kind = (row.get("type") or "IMAGE").upper()
    cap = row["caption"]
    if kind == "CAROUSEL":
        kids = []
        for name in row["image"].split("|"):
            k = call("POST", f"{USER}/media", image_url=img_url(name), is_carousel_item="true")["id"]
            kids.append(k)
        for k in kids:
            wait_ready(k)
        c = call("POST", f"{USER}/media", media_type="CAROUSEL", children=",".join(kids), caption=cap)["id"]
        wait_ready(c)
    elif kind == "REELS":
        cover = row["image"].rsplit(".", 1)[0] + "_cover.jpg"     # optional custom thumbnail
        extra = {"cover_url": img_url(cover)} if os.path.exists(os.path.join("posts", cover)) else {}
        for url in (vid_url(row["image"]), img_url(row["image"])):   # CDN first, raw GitHub as fallback
            try:
                c = call("POST", f"{USER}/media", media_type="REELS", video_url=url,
                         caption=cap, share_to_feed="true", **extra)["id"]
                wait_ready(c, tries=60, every=10)
                break
            except RuntimeError as e:
                print("Reel attempt failed:", e); err = e
        else:
            raise err
    else:
        c = call("POST", f"{USER}/media", image_url=img_url(row["image"]), caption=cap)["id"]
        wait_ready(c, tries=20, every=6)
    return call("POST", f"{USER}/media_publish", creation_id=c)


me = call("GET", USER, fields="username")
print("Connected as @" + me.get("username", "?"))

rows = list(csv.DictReader(open("schedule.csv", newline="", encoding="utf-8")))
now = datetime.datetime.utcnow()
def when(r):
    try:
        return datetime.datetime.fromisoformat(f"{r['date']}T{r['time']}")
    except (TypeError, ValueError):
        return None   # a broken line in the schedule is skipped, not fatal

rows = [r for r in rows if r.get("n") and r.get("date")]
due = [r for r in rows if not r["posted"] and when(r) and when(r) <= now]
if not due:
    print("Nothing due.")
    sys.exit(0)

def recent_captions():
    """Captions of the last few Instagram posts, so a quote is never posted twice."""
    try:
        data = call("GET", f"{USER}/media", fields="caption", limit="10").get("data", [])
        return [(m.get("caption") or "")[:80] for m in data]
    except Exception as e:
        print("Could not read recent posts:", e); return []

failed = False
for row in due[:MAX_PER_RUN]:
    print("Posting", row["n"], row.get("type") or "IMAGE", row["image"][:80])
    if DRY:
        print(row["caption"][:200]); continue
    if row["caption"][:80] in recent_captions():
        print("Already on Instagram - marking as posted, not posting again.")
        row["posted"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"); continue
    try:
        print("Published", publish(row))
        row["posted"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        print("FAILED:", e); failed = True
    time.sleep(5)

with open("schedule.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
sys.exit(1 if failed else 0)
