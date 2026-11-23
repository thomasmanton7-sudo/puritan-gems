"""Posts the next due quote card from schedule.csv to Instagram.

Runs inside GitHub Actions. Needs two repository secrets:
  IG_USER_ID    - your Instagram professional account ID
  IG_TOKEN      - a long-lived Instagram access token
Images are served from this (public) repo via raw.githubusercontent.com.
"""
import csv, datetime, json, os, sys, time, urllib.parse, urllib.request

HOST = os.environ.get("IG_HOST", "https://graph.facebook.com/v21.0")
USER = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]          # e.g. someone/puritan-gems
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
DRY = os.environ.get("DRY_RUN") == "1"


def call(method, path, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{HOST}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Instagram API error {e.code}: {e.read().decode()}")


rows = list(csv.DictReader(open("schedule.csv", newline="", encoding="utf-8")))
now = datetime.datetime.utcnow()
due = [r for r in rows if not r["posted"]
       and datetime.datetime.fromisoformat(f"{r['date']}T{r['time']}") <= now]
if not due:
    print("Nothing due.")
    sys.exit(0)

row = due[0]  # one post per run; anything missed is caught up next run
img = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/posts/{row['image']}"
print("Posting", row["n"], img)
if DRY:
    print(row["caption"])
    sys.exit(0)

c = call("POST", f"{USER}/media", image_url=img, caption=row["caption"])["id"]
for _ in range(20):                      # wait until Instagram has fetched the image
    if call("GET", c, fields="status_code").get("status_code") == "FINISHED":
        break
    time.sleep(6)
pub = call("POST", f"{USER}/media_publish", creation_id=c)
print("Published", pub)

row["posted"] = now.strftime("%Y-%m-%d %H:%M")
with open("schedule.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
