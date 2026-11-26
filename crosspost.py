"""Copies each Instagram post to a Facebook Page, Threads and X, after Instagram has posted it.

Runs in GitHub Actions right after post.py. Each platform switches on only when its secrets exist:
  Facebook:  FB_PAGE_ID, FB_PAGE_TOKEN            (a Page token made from a long-lived user token never expires)
  Threads:   TH_USER_ID, TH_TOKEN, TH_KEY         (TH_KEY = any long passphrase you make up)

Threads tokens last 60 days. This script refreshes the token every ~3 weeks and keeps the fresh one
in th_token.enc, encrypted with TH_KEY, so it never runs out.

Progress lives in crosspost_state.json: which Instagram posts each platform has copied.
A platform only copies posts Instagram made after that platform was switched on (no backlog flood).
  X:         X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET  (pay-per-use credits; Reels are skipped)
A failure on Facebook, Threads or X never affects Instagram.
"""
import csv, datetime, json, os, subprocess, sys, time, urllib.parse, urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
DRY = os.environ.get("DRY_RUN") == "1"
STATE = "crosspost_state.json"
MAX_PER_RUN = 2          # lets a platform catch up after a missed run, without flooding
NOW = datetime.datetime.utcnow()


def img_url(name):
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/posts/{name}"


def vid_url(name):
    return f"https://cdn.jsdelivr.net/gh/{REPO}@{BRANCH}/posts/{name}"


def http(method, url, params):
    data = urllib.parse.urlencode(params).encode()
    req = (urllib.request.Request(url + "?" + data.decode()) if method == "GET"
           else urllib.request.Request(url, data=data, method="POST"))
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code}: {e.read().decode()[:400]}")


def short_text(row):
    """Threads allows 500 characters: the quote and who said it."""
    q, a, s = row.get("quote", "").strip(), row.get("author", "").strip(), row.get("source", "").strip()
    if not q:                                   # carousels/reels: use the first part of the caption
        return row["caption"].split("\n#")[0].strip()[:490]
    t = f"“{q}”\n\n— {a}" + (f", {s}" if s else "")
    return t[:500]


# ---------------- Facebook Page ----------------
class Facebook:
    name = "facebook"

    def __init__(self):
        self.page, self.token = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
        self.on = bool(self.page and self.token)
        self.api = "https://graph.facebook.com/v21.0"

    def call(self, method, path, **p):
        p["access_token"] = self.token
        return http(method, f"{self.api}/{path}", p)

    def hello(self):
        info = self.call("GET", self.page, fields="name,access_token")
        print("Facebook Page:", info.get("name"))
        if info.get("access_token"):        # secret was a user token: swap in the Page's own token
            self.token = info["access_token"]
            print("Facebook: using the Page token")

    def publish(self, row):
        kind = (row.get("type") or "IMAGE").upper()
        cap = row["caption"]
        if kind == "CAROUSEL":
            ids = [self.call("POST", f"{self.page}/photos", url=img_url(n), published="false")["id"]
                   for n in row["image"].split("|")]
            media = json.dumps([{"media_fbid": i} for i in ids])
            return self.call("POST", f"{self.page}/feed", message=cap, attached_media=media)
        if kind == "REELS":
            return self.call("POST", f"{self.page}/videos", file_url=vid_url(row["image"]), description=cap)
        return self.call("POST", f"{self.page}/photos", url=img_url(row["image"]), caption=cap)


# ---------------- Threads ----------------
class Threads:
    name = "threads"

    def __init__(self, state):
        self.user, self.key = os.environ.get("TH_USER_ID"), os.environ.get("TH_KEY")
        self.state = state
        self.api = "https://graph.threads.net/v1.0"
        self.token = self.load_token() if self.key else None
        self.on = bool(self.user and self.token)

    def load_token(self):
        if os.path.exists("th_token.enc"):
            try:
                return subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-a", "-A",
                                       "-pass", "env:TH_KEY", "-in", "th_token.enc"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            except subprocess.CalledProcessError:
                print("Threads: stored token could not be decrypted; using TH_TOKEN secret")
        return os.environ.get("TH_TOKEN")

    def save_token(self, tok):
        subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-A",
                        "-pass", "env:TH_KEY", "-out", "th_token.enc"], input=tok, check=True, text=True)

    def maybe_refresh(self):
        last = self.state.get("threads_token_refreshed")
        if last and (NOW - datetime.datetime.fromisoformat(last)).days < 20:
            return
        if DRY:
            print("Threads: token refresh due (skipped in dry run)"); return
        r = http("GET", "https://graph.threads.net/refresh_access_token",
                 {"grant_type": "th_refresh_token", "access_token": self.token})
        self.token = r["access_token"]
        self.save_token(self.token)
        self.state["threads_token_refreshed"] = NOW.isoformat(timespec="minutes")
        print("Threads: token refreshed, good for another", r.get("expires_in", 0) // 86400, "days")

    def call(self, method, path, **p):
        p["access_token"] = self.token
        return http(method, f"{self.api}/{path}", p)

    def hello(self):
        print("Threads: @" + self.call("GET", "me", fields="username").get("username", "?"))
        try:
            self.maybe_refresh()
        except Exception as e:      # e.g. token under 24h old can't be refreshed yet; try again next run
            print("Threads: token refresh not done this time:", e)

    def wait(self, cid, tries=30, every=6):
        for _ in range(tries):
            st = self.call("GET", cid, fields="status").get("status")
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Threads could not process {cid}")
            time.sleep(every)

    def publish(self, row):
        kind = (row.get("type") or "IMAGE").upper()
        text = short_text(row)
        if kind == "CAROUSEL":
            kids = [self.call("POST", f"{self.user}/threads", media_type="IMAGE", image_url=img_url(n),
                              is_carousel_item="true")["id"] for n in row["image"].split("|")[:20]]
            for k in kids:
                self.wait(k)
            c = self.call("POST", f"{self.user}/threads", media_type="CAROUSEL",
                          children=",".join(kids), text=text)["id"]
        elif kind == "REELS":
            c = self.call("POST", f"{self.user}/threads", media_type="VIDEO",
                          video_url=vid_url(row["image"]), text=text)["id"]
            self.wait(c, tries=60, every=10)
        else:
            c = self.call("POST", f"{self.user}/threads", media_type="IMAGE",
                          image_url=img_url(row["image"]), text=text)["id"]
        self.wait(c)
        return self.call("POST", f"{self.user}/threads_publish", creation_id=c)



# ---------------- X (Twitter) ----------------
class X:
    name = "x"

    def __init__(self):
        e = os.environ.get
        self.ck, self.cs, self.at, self.ats = e("X_API_KEY"), e("X_API_SECRET"), e("X_ACCESS_TOKEN"), e("X_ACCESS_SECRET")
        self.on = all([self.ck, self.cs, self.at, self.ats])

    def auth(self, method, url):
        import base64, hashlib, hmac, secrets
        q = lambda v: urllib.parse.quote(str(v), safe="~")
        o = {"oauth_consumer_key": self.ck, "oauth_nonce": secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
             "oauth_token": self.at, "oauth_version": "1.0"}
        base = "&".join([method, q(url), q("&".join(f"{q(k)}={q(v)}" for k, v in sorted(o.items())))])
        key = f"{q(self.cs)}&{q(self.ats)}".encode()
        o["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
        return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in o.items())

    def req(self, method, url, body=None, ctype=None):
        h = {"Authorization": self.auth(method, url)}
        if ctype: h["Content-Type"] = ctype
        r = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{e.code}: {e.read().decode()[:400]}")

    def hello(self):
        print("X: @" + self.req("GET", "https://api.x.com/2/users/me")["data"]["username"])

    def upload(self, path):
        boundary = "----pg" + str(int(time.time() * 1000))
        data = open(path, "rb").read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"media_category\"\r\n\r\ntweet_image\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"card.jpg\"\r\n"
                f"Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        r = self.req("POST", "https://api.x.com/2/media/upload", body, f"multipart/form-data; boundary={boundary}")
        return (r.get("data") or r).get("id") or r.get("media_id_string")

    def publish(self, row):
        kind = (row.get("type") or "IMAGE").upper()
        if kind == "REELS":
            return "skipped (videos are not copied to X)"
        names = row["image"].split("|")[:4]
        ids = [self.upload("posts/" + n) for n in names]
        t = short_text(row)
        if len(t) > 275:       # the card carries the full quote
            a, s = row.get("author", ""), row.get("source", "")
            t = (f"\u2014 {a}" + (f", {s}" if s else "")) if a else ""
        body = json.dumps({"text": t, "media": {"media_ids": ids}}).encode()
        return self.req("POST", "https://api.x.com/2/tweets", body, "application/json")

# ---------------- main ----------------
state = json.load(open(STATE)) if os.path.exists(STATE) else {}
rows = list(csv.DictReader(open("schedule.csv", newline="", encoding="utf-8")))
ig_done = [r for r in rows if r["posted"][:4].isdigit()]
ig_done.sort(key=lambda r: r["posted"])
failed = False

for p in (Facebook(), Threads(state), X()):
    if not p.on:
        print(f"{p.name}: not set up yet, skipping"); continue
    s = state.setdefault(p.name, {"since": NOW.strftime("%Y-%m-%d %H:%M"), "done": []})
    try:
        p.hello()
    except Exception as e:
        print(f"{p.name}: cannot connect: {e}"); failed = True; continue
    todo = [r for r in ig_done if r["posted"] >= s["since"] and r["image"] not in s["done"]]
    if not todo:
        print(f"{p.name}: up to date (copying Instagram posts made since {s['since']} UTC)")
    for row in todo[:MAX_PER_RUN]:
        print(f"{p.name}: posting", row.get("type") or "IMAGE", row["image"][:60])
        if DRY:
            print("   ", short_text(row)[:120].replace("\n", " ")); continue
        try:
            print("   published", p.publish(row))
            s["done"].append(row["image"])
        except Exception as e:
            print(f"   FAILED: {e}"); failed = True
        time.sleep(5)

if not DRY:
    json.dump(state, open(STATE, "w"), indent=1)
sys.exit(1 if failed else 0)
