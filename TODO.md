# Highlightz — Pre-Launch To-Do List

---

## Must Have Before Selling

### Reliability
- [ ] Bot auto-recovers when a stream goes offline — currently crashes and needs manual restart if a streamer goes offline mid-session
- [ ] Fix Dockerfile — containers don't work because streamlink isn't installed; moving servers will break everything
- [ ] Fix blocking audio probe — `subprocess.run()` on the event loop stalls the whole bot during audio checks

### Multi-Tenant (required to sell to multiple people)
- [ ] Per-user accounts — right now there is one shared password and everyone sees everyone's clips
- [ ] Per-user stream isolation — each customer should only see their own streams and clips
- [ ] Stripe billing — paywall the dashboard behind an active subscription

### Basic UX for Customers
- [ ] Onboarding flow — new user signs up and gets walked through adding their first stream and Discord webhook
- [ ] Error messaging in dashboard — if a stream fails to start the user needs to see why (currently silent)
- [ ] Mobile-friendly dashboard — currently desktop only

---

## Should Have (improves sales and retention)
- [ ] Clip storage limit per user — prevent one customer from filling the disk
- [ ] Auto-delete old approved clips after X days — disk management
- [ ] Email notifications — alert user when a clip is ready, not just Discord
- [ ] Clip download button — let users download the MP4 directly from the dashboard
- [ ] Usage stats page — show customers their clips per week, top moments, approval rate

---

## Nice to Have
- [ ] Discord OAuth login — let users sign in with Discord instead of a password
- [ ] Multiple Discord channels per stream — post to different channels based on clip quality
- [ ] TikTok / Shorts vertical crop — auto-generate 9:16 version of every clip
- [ ] Public clip gallery — shareable link for each approved clip

---

## Notes
- Minimum viable launch requires: multi-tenant accounts + Stripe billing
- Fix everything in Must Have first or you will spend all your time on customer support
