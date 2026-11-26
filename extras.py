"""Extra Instagram reach, made automatically from the cards already in posts/.

1. STORY: every quote card Instagram posts is also put in the Story (9:16, card on a soft
   blurred background), straight after the feed post.
2. REELS: on Tuesdays, Thursdays and Saturdays (in the 15:41 run) a 10-second Reel is made from a
   card posted at least a week earlier: slow push-in, fade, soft pad music. Saturdays are skipped
   while the hand-made Saturday Reels in schedule.csv are still running.

Files are rendered on the GitHub runner, committed to media/, and fetched by Instagram from that
exact commit. Progress lives in extras_state.json. Failures here never affect the feed posts.
Needs: IG_USER_ID, IG_TOKEN (same as post.py); Pillow and ffmpeg on the runner.
"""
import csv, datetime, json, os, random, subprocess, sys, time, urllib.parse, urllib.request
from PIL import Image, ImageFilter, ImageEnhance

HOST = os.environ.get("IG_HOST", "https://graph.facebook.com/v21.0")
USER, TOKEN = os.environ["IG_USER_ID"], os.environ["IG_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
DRY = os.environ.get("DRY_RUN") == "1"
NOW = datetime.datetime.utcnow()
STATE = "extras_state.json"
REEL_DAYS = {1, 3, 5}           # Tue, Thu, Sat (Mon=0)


def call(method, path, **p):
    p["access_token"] = TOKEN
    data = urllib.parse.urlencode(p).encode()
    url = f"{HOST}/{path}"
    req = (urllib.request.Request(url + "?" + data.decode()) if method == "GET"
           else urllib.request.Request(url, data=data, method="POST"))
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Instagram API error {e.code}: {e.read().decode()[:300]}")


def wait_ready(cid, tries=40, every=8):
    for _ in range(tries):
        st = call("GET", cid, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st == "ERROR":
            raise RuntimeError(f"Instagram could not process {cid}")
        time.sleep(every)
    raise RuntimeError(f"{cid} not ready in time")


def git(*a):
    return subprocess.run(["git", *a], check=True, capture_output=True, text=True).stdout.strip()


def push(path, msg):
    """Commit one file and return the commit sha, so Instagram can fetch that exact version."""
    git("add", path)
    git("commit", "-m", msg)
    try:
        git("push")
    except subprocess.CalledProcessError:
        git("pull", "--rebase", "--autostash")
        git("push")
    sha = git("rev-parse", "HEAD")
    time.sleep(5)
    return sha


# ---------- rendering ----------
def story_image(card_path, out):
    card = Image.open(card_path).convert("RGB")
    bg = card.resize((1080, int(1080 * card.height / card.width))).resize((1536, 1920))
    bg = bg.crop((228, 0, 1308, 1920)).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    w = 960
    c = card.resize((w, int(w * card.height / card.width)), Image.LANCZOS)
    bg.paste(c, ((1080 - w) // 2, (1920 - c.height) // 2))
    bg.save(out, quality=88)


def reel_video(card_path, out, secs=10):
    still = out.replace(".mp4", "_still.jpg")
    card = Image.open(card_path).convert("RGB")
    edge = card.getpixel((8, 8))
    canvas = Image.new("RGB", (1080, 1920), edge)
    c = card.resize((1080, int(1080 * card.height / card.width)), Image.LANCZOS)
    canvas.paste(c, (0, (1920 - c.height) // 2))
    canvas.save(still, quality=92)
    fps, n = 30, secs * 30
    vf = (f"scale=2160:3840,zoompan=z='1+0.06*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={n}:s=1080x1920:fps={fps},fade=t=in:st=0:d=0.8,fade=t=out:st={secs-0.8}:d=0.8,format=yuv420p")
    base = random.choice([(110, 164.81, 220, 277.18), (98, 146.83, 196, 246.94), (130.81, 196, 261.63, 329.63)])
    af = (f"aevalsrc='0.05*sin(2*PI*{base[0]}*t)+0.035*sin(2*PI*{base[1]}*t)+0.03*sin(2*PI*{base[2]}*t)"
          f"+0.02*sin(2*PI*{base[3]}*t)*(0.6+0.4*sin(2*PI*0.2*t))':s=44100:d={secs},afade=t=in:d=1.5,"
          f"afade=t=out:st={secs-2}:d=2,lowpass=f=1200,volume=1.6")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", still, "-f", "lavfi", "-i", af,
                    "-vf", vf, "-t", str(secs), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                    "-r", str(fps), "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest",
                    "-movflags", "+faststart", out], check=True)
    os.remove(still)


# ---------- main ----------
state = json.load(open(STATE)) if os.path.exists(STATE) else {}
state.setdefault("stories_since", (NOW - datetime.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M"))
state.setdefault("stories", [])
state.setdefault("reels", {})
rows = list(csv.DictReader(open("schedule.csv", newline="", encoding="utf-8")))
posted = [r for r in rows if r["posted"][:4].isdigit() and (r.get("type") or "IMAGE").upper() == "IMAGE"
          and os.path.exists("posts/" + r["image"])]
os.makedirs("media", exist_ok=True)
failed = False

# 1) Story for each newly posted card
for r in [r for r in posted if r["posted"] >= state["stories_since"] and r["image"] not in state["stories"]][-1:]:
    out = f"media/story_{r['image']}"
    try:
        story_image("posts/" + r["image"], out)
        print("Story made for", r["image"])
        if DRY:
            continue
        sha = push(out, f"story {r['image']}")
        c = call("POST", f"{USER}/media", media_type="STORIES",
                 image_url=f"https://raw.githubusercontent.com/{REPO}/{sha}/{out}")["id"]
        wait_ready(c, tries=20, every=6)
        print("Story published", call("POST", f"{USER}/media_publish", creation_id=c))
        state["stories"].append(r["image"])
    except Exception as e:
        print("Story FAILED:", e); failed = True

# 2) Reel on Tue/Thu/Sat afternoons
today = NOW.strftime("%Y-%m-%d")
in_reel_slot = NOW.hour >= 15 or os.environ.get("FORCE_REEL") == "1"
sched_reel_today = any(r["date"] == today and (r.get("type") or "").upper() == "REELS" for r in rows)
if NOW.weekday() in REEL_DAYS and in_reel_slot and today not in state["reels"] and not sched_reel_today:
    used = set(state["reels"].values())
    week_ago = (NOW - datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    pool = [r for r in posted if r["image"] not in used and r["posted"] <= week_ago] or \
           [r for r in posted if r["image"] not in used and r["posted"][:10] != today]
    if pool:
        r = random.choice(pool[:40])          # favour older cards first
        out = f"media/reel_{r['image'].rsplit('.', 1)[0]}.mp4"
        try:
            reel_video("posts/" + r["image"], out)
            print("Reel made from", r["image"])
            if not DRY:
                sha = push(out, f"reel {r['image']}")
                for url in (f"https://cdn.jsdelivr.net/gh/{REPO}@{sha}/{out}",
                            f"https://raw.githubusercontent.com/{REPO}/{sha}/{out}"):
                    try:
                        c = call("POST", f"{USER}/media", media_type="REELS", video_url=url,
                                 caption=r["caption"], share_to_feed="true")["id"]
                        wait_ready(c, tries=60, every=10)
                        break
                    except RuntimeError as e:
                        print("Reel attempt failed:", e); err = e
                else:
                    raise err
                print("Reel published", call("POST", f"{USER}/media_publish", creation_id=c))
                state["reels"][today] = r["image"]
        except Exception as e:
            print("Reel FAILED:", e); failed = True
    else:
        print("No card available for a Reel yet")
else:
    print("No Reel this run")

if not DRY:
    json.dump(state, open(STATE, "w"), indent=1)
sys.exit(1 if failed else 0)
