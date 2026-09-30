# TikTok app review: answers and demo script

App name: **Highlightz**. Web: https://highlightz.app. Terms: https://highlightz.app/tos.
Privacy: https://highlightz.app/privacy. Login Kit redirect URI:
https://highlightz.app/publish/connect/tiktok/callback.
Products: Login Kit, Content Posting API (Direct Post, FILE_UPLOAD).
Scopes: `user.info.basic`, `video.publish`.

## Short description (max 120 characters)
Highlightz catches Twitch and Kick stream highlights; you edit a clip and post it to your own TikTok.

## How each product / scope is used
- **Login Kit / user.info.basic:** the user connects their own TikTok account from
  the Account page. We show their TikTok name and avatar on the posting screen so
  they can see which account they are posting to.
- **Content Posting API, Direct Post / video.publish:** the user picks a clip in
  their Clip Library, presses Post, chooses TikTok, and sees the posting screen
  (their name, a preview, the title, who can view with no default, comments/duet/
  stitch off by default, the commercial-content toggle off by default, and the
  Music Usage Confirmation). They press Post. Only then is the video uploaded to
  TikTok. **Nothing is ever posted to TikTok automatically, on a schedule, or in
  bulk: every TikTok post is one person pressing Post.**

## Behaviour we can state
- Posts go only to the connected user's own account, after they press Post.
- Privacy level comes from `creator_info`; nothing is preselected.
- Comment/duet/stitch are off by default and greyed out when the creator's TikTok
  settings disable them.
- Commercial content is off by default; "Your brand" / "Branded content"; posting
  is blocked if it is on with neither chosen; branded content cannot be private.
- No watermark, logo or promotional text is added; the video is what the user edited.
- Processing notice shown after posting.
- Tokens are encrypted at rest, removed on Disconnect and on account deletion.
- Until the app is audited, posts are private (SELF_ONLY), and the screen says so.

## Demo video script (MP4/MOV, under 50 MB, on the real domain highlightz.app)
1. Log in at highlightz.app. Open **Account**: show TikTok connected (name shown).
2. Open **Clip Library**, pick a clip, press **Post**. Tick **TikTok**.
3. Walk the screen out loud: account name/avatar, preview, title, the Who-can-view
   dropdown (nothing preselected; pick one), the three interaction boxes (off),
   the commercial-content toggle (off; switch on and show Your brand / Branded
   content and the disabled Post, then switch off), the music-usage line.
4. Press **Post now**. Show the processing notice.
5. Open TikTok on the same account and show the post arriving (private until audit).
6. Say: there is no automatic or scheduled TikTok posting; each one is a press of Post.

Note for reviewers: the public site lists Scheduler/posting as "coming soon" because
posting is in private beta for approved accounts.
