# AI Radar

Every morning at about 5 AM IST, GitHub runs `radar.py` in the cloud. It:

1. Reads about 23 sources: OpenAI, Anthropic, Google, Mistral, xAI and DeepSeek announcements, Hugging Face, Hacker News, Reddit, TechCrunch, The Verge, The Decoder and Simon Willison's blog.
2. Asks Gemini (free tier) to search the web for anything those sources missed.
3. Asks Gemini to pick the 3 best video topics, each with a hook, an angle, a format, a theme, key facts, sources and claims to verify. It flags "2-reel days" when something big launches.
4. Saves the digest in `digests/YYYY-MM-DD.md`, which becomes your archive.
5. Emails it to you.

It costs nothing: GitHub Actions free minutes, the Gemini API free tier and Gmail. Your PC and VPS are not involved.

## One-time setup (about 15 minutes)

You do these steps yourself because they involve your passwords and keys.

1. **Create the repo.** Sign in to github.com with **your own account**, click New repository, name it `ai-radar`, choose **Private**, and leave it empty (no README).
2. **Push this folder.** Claude does this once you send the repo URL, your GitHub username, and the email you want on commits.
   - This PC's git is logged in as a friend's GitHub account. That login stays untouched.
   - Claude puts *your* username in the repo address (`https://<your-username>@github.com/<your-username>/ai-radar.git`). Git Credential Manager then asks for your account for this repo only.
   - A GitHub sign-in window opens. **You** sign in with your own account (choose "sign in with another account" if it offers your friend's). It's saved separately.
   - Commit name and email are set for this repo only, so your friend's global git settings don't change.
   - Fallback with no login on this PC: on the empty repo page, click "uploading an existing file" and drag in the contents of this folder, including the `.github` folder.
3. **Get a Gemini API key.** Go to https://aistudio.google.com, click Get API key, then Create API key. It's free.
4. **Create a Gmail app password.** Go to https://myaccount.google.com/security. Turn on 2-Step Verification if it's off, then open App passwords, create one named "AI Radar", and copy the 16-character code.
5. **Add four secrets.** In the repo, go to Settings → Secrets and variables → Actions → New repository secret:

   | Name | Value |
   | --- | --- |
   | `GEMINI_API_KEY` | the key from step 3 |
   | `GMAIL_USER` | the Gmail address that sends the email (simplest: `tarunaitools@gmail.com` sending to itself) |
   | `GMAIL_APP_PASSWORD` | the 16-character app password, created on that same Google account in step 4 |
   | `RADAR_TO` | `tarunaitools@gmail.com` |

6. **Test it.** Open the Actions tab, click AI Radar, then Run workflow. The email arrives in 2–3 minutes.

## Notes

- The first run records the article lists of Anthropic, Mistral, xAI, DeepSeek and Hugging Face trending without reporting them. New posts show up from the second run on.
- Reddit often blocks cloud servers. Blocked sources are listed at the bottom of each email; the other sources still work.
- Free-tier Gemini requests may be used by Google to improve its products. That's fine for public news, but don't put private data through this key.
- To change the time, edit the `cron` line in `.github/workflows/radar.yml` (times are UTC; IST is UTC+5:30).
- To run it on your PC: `python radar.py --dry-run` (add `--no-llm` to skip Gemini).
