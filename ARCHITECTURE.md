# Highlightz — Architecture Diagrams

---

## 1. System Overview — What Lives Where

```mermaid
flowchart TB
    subgraph BROWSER["🌐 User Browser"]
        UI["React SPA\n(Babel standalone, no bundler)\nDashboard · Clips · Streams · Settings\nFeedback · Account"]
        WSC["WebSocket Client\nreceives live score_update,\nclip_ready, stream_status"]
    end

    subgraph VPS["🖥️  VPS  —  /opt/highlightz  (systemd: highlightz.service, runs as root)"]
        direction TB

        subgraph PROC["Single Python Process  ·  asyncio event loop  ·  src/main.py"]

            subgraph STATIC_TASKS["4 Persistent background tasks (asyncio.gather)"]
                UV["Uvicorn / FastAPI\n0.0.0.0:8000\nsrc/dashboard/api.py"]
                RL["Redis Pub/Sub Listener\nsuperclipbot:new_streams\nsuperclipbot:remove_streams"]
                CP_LOOP["Clip Processor Loop\nrun_clip_processor()\nblocks on JobQueue.pop()"]
                AD["Auto-Delete Task\nruns daily\nremoves approved clips > 30 days"]
            end

            subgraph PER_STREAM["Per-Stream Worker  ·  1 StreamWorker per monitored channel  (max 20)"]
                direction LR
                SW["StreamWorker\n_run_session()"]
                subgraph WTASKS["5 concurrent tasks inside each worker"]
                    T1["_run_chat()\nTwitchChatMonitor IRC"]
                    T2["TriggerEngine\nrun_evaluation_loop()\nevery 1 second"]
                    T3["_profile_update_loop()\nevery 5s (learning)\n30s (calibrated)"]
                    T4["_liveness_check()\nevery 60s"]
                    T5["_viewer_poll_loop()\nevery 60s"]
                end
                AM["AudioMeter\nstreamlink subprocess\n  → FFmpeg subprocess\n  → Python RMS dBFS"]
                TE["TriggerEngine\n6 signals → composite score"]
                CM["ChatMetrics\n15s sliding window"]
                SP["StreamerProfile\nper-channel baselines\nadaptive threshold"]
            end

            subgraph CLIP_PIPELINE["Clip Pipeline"]
                CP["ClipProcessor\nprocess(ClipJob)\ntwitch_clips.create_clip()"]
            end

        end

        REDIS[("Redis :6379\n─────────────\nList: superclipbot:clip_jobs\nPub: superclipbot:new_streams\nPub: superclipbot:remove_streams")]

        subgraph FS["Local Filesystem  ·  ./clips/"]
            direction LR
            F1["streams.json\nclips.json\nusers.json\nusers.json.bak"]
            F2["feedback.json\noptout.json"]
            F3["profiles/\n  {user_id}/\n    {channel}.json"]
        end
    end

    subgraph EXT["☁️  External Services"]
        direction LR
        TH["Twitch Helix API\napi.twitch.tv/helix\n─────────────\n• /streams  (viewer count, title)\n• /users    (broadcaster ID)\n• /clips    (POST create, GET fetch)\n• /token    (client credentials)"]
        TO["Twitch OAuth2\nid.twitch.tv\n─────────────\n• /authorize  (consent screen)\n• /token      (code exchange)\n• /token      (refresh)"]
        TI["Twitch IRC Chat\nirc-ws.chat.twitch.tv:443\n─────────────\n• PRIVMSG  (chat messages)\n• USERNOTICE  (subs/raids)"]
        STR["Stripe\napi.stripe.com\n─────────────\n• checkout.sessions.create\n• billing_portal.sessions.create\n• subscriptions.list / cancel\n• Webhook: POST /billing/webhook"]
    end

    UI -- "HTTPS REST" --> UV
    WSC -- "WSS /ws" --> UV
    UV -- "broadcasts score/clip/status events" --> WSC
    UV -- "publish new/remove stream" --> REDIS
    RL -- "subscribe" --> REDIS
    RL -- "spawn_worker()" --> PER_STREAM
    CP_LOOP -- "BLPOP 5s timeout" --> REDIS
    TE -- "score ≥ threshold\nClipJob → RPUSH" --> REDIS
    CP_LOOP --> CP
    CP -- "create_clip(user_oauth_token, broadcaster_id)" --> TH
    CP -- "notify_clip_ready() → WS broadcast" --> UV

    SW --> AM
    SW --> TE
    SW --> CM
    SW --> SP
    CM -- "ChatSnapshot every 1s" --> TE
    AM -- "get_audio_level_db() every 1s" --> TE
    T5 -- "viewer_count" --> TE
    T4 -- "is_live?" --> TH
    T5 -- "get_stream_info()" --> TH
    T1 -- "IRC WebSocket" --> TI
    AM -- "stream URL" --> TH

    UV -- "OAuth flow" --> TO
    UV -- "checkout / webhook" --> STR
    STR -- "POST /billing/webhook" --> UV

    UV -- "read/write" --> FS
    T3 -- "save profile" --> F3
    SP -- "load/save JSON" --> F3
```

---

## 2. Clip Pipeline — End to End

```mermaid
sequenceDiagram
    actor User as 👤 User (Browser)
    participant API as FastAPI /streams
    participant Redis as Redis Pub/Sub
    participant Main as main.py listener
    participant SW as StreamWorker
    participant AM as AudioMeter
    participant IRC as Twitch IRC
    participant TE as TriggerEngine
    participant JQ as JobQueue (Redis List)
    participant CP as ClipProcessor
    participant TW as Twitch Helix API
    participant WS as WebSocket → Browser

    User->>API: POST /streams {channel, platform, preset}
    API->>API: validate, check opt-out, save streams.json
    API->>Redis: PUBLISH new_streams {channel, user_id, preset}
    Redis-->>Main: message received
    Main->>SW: spawn_worker() → asyncio.Task

    par 5 concurrent tasks inside StreamWorker
        SW->>IRC: connect IRC WebSocket (TwitchChatMonitor)
        SW->>AM: AudioMeter.start()
        Note over AM: streamlink → FFmpeg pipe<br/>reads 0.2s PCM chunks<br/>computes dBFS RMS
        SW->>TE: run_evaluation_loop() every 1s
        SW->>SW: _viewer_poll_loop() every 60s
        SW->>SW: _liveness_check() every 60s
    end

    loop Every ~1s
        TE->>IRC: ChatMetrics.snapshot() → velocity, keywords
        TE->>AM: get_audio_level_db() → dBFS
        TE->>TE: compute 6 signals × weights → score 0-100
        TE->>WS: score_update {score, breakdown, audio_db, viewers}
    end

    Note over IRC: Chat explodes, audio spikes
    TE->>TE: score (e.g. 87) ≥ threshold (60) AND not in cooldown
    TE->>JQ: RPUSH ClipJob {channel, trigger_score, signals, chat_snapshot, user_id}

    CP->>JQ: BLPOP (blocking, 5s timeout)
    JQ-->>CP: ClipJob dequeued
    CP->>CP: get_valid_twitch_token(user_id) — decrypt + refresh if expired
    CP->>TW: GET /helix/users?login={channel} → broadcaster_id
    CP->>TW: POST /helix/clips?broadcaster_id={id}  [User OAuth token, clips:edit scope]
    TW-->>CP: 202 {id: "AbcXyz123..."} (clip slug)
    CP->>TW: GET /helix/clips?id={slug} (poll up to 8× with 2s delay)
    TW-->>CP: clip {url, embed_url, thumbnail_url, duration}
    CP->>CP: save to clips.json (status="pending")
    CP->>WS: notify_clip_ready() → broadcast clip_ready to user's WebSocket(s)
    WS-->>User: clip appears in Review queue
```

---

## 3. Authentication & Session Flow

```mermaid
flowchart TD
    A["User visits https://highlightz.app"] --> B{Session cookie\nvalid?}
    B -- No --> C["Redirect → /login\n(LOGIN_HTML served)"]
    B -- Yes --> D{subscription_status\nin DB?}

    C --> E["User clicks\n'Continue with Twitch'"]
    E --> F["GET /auth/twitch\n→ generate state token\n→ store in session"]
    F --> G["Redirect to Twitch\nhttps://id.twitch.tv/oauth2/authorize\n?scope=clips:edit\n?redirect_uri=..."]
    G --> H["User authorizes on Twitch"]
    H --> I["GET /auth/twitch/callback\n?code=...&state=..."]
    I --> J["Exchange code\nPOST id.twitch.tv/oauth2/token\n→ {access_token, refresh_token}"]
    J --> K["GET api.twitch.tv/helix/users\n→ {id, login, display_name, avatar}"]
    K --> L{User exists\nin users.json?}

    L -- New user --> M["Create user record\nsubscription_status = 'trialing'\ntrial_ends_at = now + 7 days\nTokens encrypted with Fernet"]
    L -- Existing, status='none'\nno trial_ends_at --> N["Grant 7-day trial\nsubscription_status = 'trialing'\ntrial_ends_at = now + 7 days"]
    L -- Existing, has subscription --> O["Update tokens only\npreserve subscription_status"]

    M --> P["Set session:\nauth=True, user_id, username\nis_admin, subscription_status"]
    N --> P
    O --> P
    P --> Q["Redirect → /"]

    D -- active or trialing\n(and trial not expired) --> R["✅ Access granted\n→ Dashboard HTML"]
    D -- expired / none / canceled --> S["Redirect → /billing/paywall"]

    S --> T["User clicks Subscribe"]
    T --> U["GET /billing/checkout\n→ create Stripe Checkout session\n→ redirect to stripe.com"]
    U --> V["User pays on Stripe"]
    V --> W["Stripe POST /billing/webhook\ntype: customer.subscription.updated\nstatus: active"]
    W --> X["Update users.json\nsubscription_status = 'active'\nstripe_customer_id = cust_..."]
    X --> R

    style M fill:#1a3a1a,stroke:#22c55e,color:#86efac
    style N fill:#1a3a1a,stroke:#22c55e,color:#86efac
    style R fill:#0f2a0f,stroke:#22c55e,color:#86efac
    style S fill:#2a0f0f,stroke:#ef4444,color:#fca5a5
```

---

## 4. Signal Scoring — How a Trigger Score is Built

```mermaid
flowchart LR
    subgraph INPUTS["Raw Inputs (sampled every 1s)"]
        I1["Twitch IRC\nmessages/sec\nkeyword hits\ntrigger phrases"]
        I2["AudioMeter\ndBFS level\n(8kHz PCM RMS)"]
        I3["Viewer count\n(polled every 60s)"]
        I4["ChatMetrics\nsilence duration\nVADER sentiment"]
    end

    subgraph SIGNALS["Signal Scoring (0.0 → 1.0 each)"]
        S1["CHAT_VELOCITY\ncurrent velocity\n÷ profile baseline\n÷ spike_multiplier"]
        S2["AUDIO_SPIKE\n30s warmup → learn baseline\nrolling peak − slow EMA\n÷ 15 dB range"]
        S3["VIEWER_SPIKE\ncurrent ÷ EMA baseline\n− 1.0, ÷ 0.5"]
        S4["KEYWORD\nhits ÷ message count\n× 3 + trigger bonus"]
        S5["SENTIMENT\nVADER abs compound\n÷ profile baseline"]
        S6["SILENCE_BURST\nchat silence duration\n→ anticipation score"]
    end

    subgraph WEIGHTS["Base Weights × Profile Multipliers"]
        W1["CHAT_VELOCITY  30%"]
        W2["AUDIO_SPIKE    25%"]
        W3["SILENCE_BURST  15%"]
        W4["KEYWORD        15%"]
        W5["SENTIMENT       8%"]
        W6["VIEWER_SPIKE    7%"]
    end

    subgraph SCORE["Composite Score"]
        RAW["Raw weighted sum"]
        MULTI["× 1.25 if ≥ 3 signals\nabove 0.25"]
        BONUS["+15 if sub/raid\nfired in last 30s"]
        FINAL["Final Score\n0 – 100"]
        THRESH{{"≥ profile.trigger_threshold\n(default 60, adaptive)\nAND not in cooldown?"}}
    end

    I1 --> S1 & S4 & S5 & S6
    I2 --> S2
    I3 --> S3
    I4 --> S6

    S1 --> W1 --> RAW
    S2 --> W2 --> RAW
    S6 --> W3 --> RAW
    S4 --> W4 --> RAW
    S5 --> W5 --> RAW
    S3 --> W6 --> RAW

    RAW --> MULTI --> BONUS --> FINAL --> THRESH

    THRESH -- Yes --> FIRE["🎬 TriggerEvent\n→ ClipJob → Redis"]
    THRESH -- No --> WAIT["Keep evaluating\n(next 1s tick)"]
```

---

## 5. Per-User Data Isolation

```mermaid
flowchart TD
    subgraph USERS["users.json  (all users)"]
        U1["user_id: abc123\ntwitch_login: streamer_fan\nsubscription_status: active\ntw_access: encrypted\ntrial_ends_at: 0"]
        U2["user_id: def456\ntwitch_login: another_user\nsubscription_status: trialing\ntrial_ends_at: 1752000000"]
    end

    subgraph U1DATA["User abc123's data"]
        S1["streams.json entry\nuser_id: abc123\nchannel: xqc"]
        C1["clips.json entries\nuser_id: abc123\n3 clips"]
        P1["profiles/abc123/xqc.json\ntrigger_threshold: 58\navg_velocity: 2.3\navg_audio_db: -22.1"]
        WS1["WebSocket set\n_ws_clients['abc123']"]
    end

    subgraph U2DATA["User def456's data"]
        S2["streams.json entry\nuser_id: def456\nchannel: summit1g"]
        C2["clips.json entries\nuser_id: def456\n1 clip"]
        P2["profiles/def456/summit1g.json\ntrigger_threshold: 62\navg_velocity: 1.8"]
        WS2["WebSocket set\n_ws_clients['def456']"]
    end

    U1 --> U1DATA
    U2 --> U2DATA

    U1DATA -- "broadcast({user_id: abc123})" --> WS1
    U2DATA -- "broadcast({user_id: def456})" --> WS2

    note1["Workers, clips, profiles,\nstreams, and WebSocket broadcasts\nare ALL scoped to user_id.\nUsers never see each other's data."]
```
