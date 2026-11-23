# @puritan.gems — automatic posting setup

78 posts are ready: 08:07 and 19:07 (UK) every day from 24 Nov 2026 to 1 Jan 2027.
Mornings are the light design. Evenings alternate green and burgundy.
Christmas morning is Manton on the incarnation.

## 1. Instagram (on your phone)
1. Create the account @puritan.gems with a new email that is used only for this account.
2. Go to Settings → Account type and tools → **Switch to professional account**. Choose **Creator** and the category "Writer" or "Personal blog".
   You do NOT need a Facebook Page for this method.

## 2. Meta developer app — DONE
App "Gems", business portfolio "Puritan Gems", Instagram user ID 17841409732087665.
The token is a system-user token that never expires.

## (old notes)
1. Go to developers.facebook.com and log in. Click **Create App**, choose the use case **Manage messaging & content on Instagram**, and give it any name.
2. In the app, open **Instagram → API setup with Instagram login**.
   1. Under "Generate access tokens", click **Add account** and log in as @puritan.gems.
   2. Click **Generate token** and copy it. This is your `IG_TOKEN`. It is long-lived and lasts 60 days.
   3. The **Instagram user ID** shown next to the account is your `IG_USER_ID`.
3. You can leave the app in Development mode. Posting to your own account works without App Review.

## 3. GitHub
1. Create a GitHub account using the same private email.
2. Create a new **Public** repository called `puritan-gems`. Public is needed so Instagram can fetch the images.
3. Click **Add file → Upload files** and drag in everything from this folder. That means `post.py`, `schedule.csv`, `SETUP.md`, the `posts` folder and the `.github` folder.
   The `.github` folder is hidden on Mac. Press Cmd+Shift+. to show it.
4. Go to Settings → Secrets and variables → Actions → **New repository secret**. Add two secrets:
   - `IG_USER_ID`
   - `IG_TOKEN`

## 4. Test it
Go to Actions → "Post to Instagram" → **Run workflow**.
- Leave dry run set to `1` to only print what it would post.
- Set it to `0` to post the next due quote for real.

After that, posting runs automatically twice a day. Each post is ticked off in `schedule.csv`.

## Notes
- **Token:** never expires. If you ever lose it, generate a new one in the app and paste it over the `IG_TOKEN` secret.
- **Adding more quotes:** drop new images in `posts/` and add rows to `schedule.csv`. Claude can make these for you.
- **Missed slots:** GitHub sometimes runs a few minutes late. If a slot is missed, it is posted on the next run.
- **Opsec:** nothing here contains your name. The only identity involved is the new email.
