# Architecture

Three views of the same system. GitHub renders these diagrams directly. To trace one real page through it in a terminal, run `./run.sh explain convert-scanned-image-to-text`.

## 1. The pipeline and the agent loop

The main line runs left to right. The red loop is the agent behaviour: a draft that fails any check goes back to the writer with the exact reasons, at most twice, before it is quarantined instead of published.

```mermaid
flowchart LR
    T([Daily schedule<br/>or ./run.sh run]) --> I
    KW[(keywords.csv)] --> I

    subgraph BEFORE["Before writing: prevent duplicates"]
        I["1 Ingest<br/><i>rules</i><br/>slug, intent, formats, free?"] --> C["2 Cluster<br/><i>embeddings</i><br/>merge / siblings / link candidates"]
        C --> P["3 Plan<br/><i>planner AI</i><br/>angle, questions, who owns which topic"]
    end

    subgraph LOOP["Write, check, revise (per page)"]
        W["4 Write<br/><i>writer AI</i><br/>10-section page as JSON"] --> Q["5a Rule + similarity checks<br/><i>code</i><br/>vs every published page"]
        Q -->|pass| E["5b Editor<br/><i>second AI</i><br/>specificity, swap test"]
        Q -->|fail: errors as instructions| W
        E -->|fail: problems as instructions| W
    end

    P --> W
    E -->|pass| S["Save page<br/>status = passed"]
    Q -. "still failing after 2 revisions" .-> X["Quarantine<br/>never published, alerted"]
    E -. "still failing after 2 revisions" .-> X

    subgraph AFTER["After writing: whole-set checks and output"]
        S --> SW["6 Corpus sweep<br/><i>code</i><br/>all pairs incl. FAQ questions"]
        SW -->|clean| R["7 Render<br/>HTML, Markdown, JSON, sitemap"]
        R --> RP["8 Report + alerts<br/>report.md, exit code, webhook, heartbeat"]
    end
    SW -. "pair too similar: revise newer page" .-> W

    F[(App + platform<br/>fact sheets)] -. "only allowed source of claims" .-> P
    F -.-> W

    classDef ai fill:#e6effb,stroke:#2a78d6,color:#0b2a52;
    classDef code fill:#e4f3ea,stroke:#1d7f47,color:#0d3a20;
    classDef bad fill:#f8e9e6,stroke:#b8402f,color:#4a140c;
    class C,P,W,E ai;
    class Q,SW,R,RP,S code;
    class X bad;
    linkStyle 6,7 stroke:#b8402f,stroke-width:2px;
```

## 2. What the pipeline remembers between runs (incremental runs)

Each page stores a hash of everything that determines its content. A run regenerates only the pages whose hash changed, which is why a run with no changes reports "0 pages to generate".

```mermaid
flowchart TB
    subgraph INPUTS["Inputs hashed per page"]
        K[keyword record]
        A[app fact sheet]
        PF["only the platform facts<br/>this page's brief cites"]
        PR[prompt templates]
        M["primary model names<br/>(fallbacks excluded)"]
    end
    INPUTS --> H{{"input hash<br/>same as stored?"}}
    H -->|yes| SKIP[skip: keep published page]
    H -->|no or new keyword| GEN[plan + write + check]
    GEN --> DB[(state/pseo.sqlite<br/>pages, shingles, embeddings,<br/>FAQ vectors, runs, events)]
    DB -->|whole corpus| CHECKS[duplicate checks for the next page]
    DB --> OUT[output/ pages + report]
```

Editing one fact in `data/platform_facts.yaml` regenerates only the pages whose brief cites that fact. Removing a keyword marks its page `retired`, which drops it from the output and the sitemap.

## 3. Running unattended

```mermaid
sequenceDiagram
    autonumber
    participant GH as GitHub Actions (cron, daily)
    participant P as pseo run
    participant G as Gemini API
    participant DB as SQLite state
    participant HB as Heartbeat service
    participant AL as Alert webhook

    GH->>P: start (secrets: API key, webhook, heartbeat URL)
    P->>HB: ping /start
    P->>DB: take run lock, load state
    loop each new or changed keyword
        P->>G: plan / write / check (rate-limited, 180 s timeout)
        alt overloaded or daily quota gone
            G-->>P: 503 / 429
            P->>G: next model in the fallback chain
        end
        P->>DB: save page, or quarantine
    end
    alt 3 consecutive failures
        P->>P: circuit breaker: stop generating, still publish what passed
    end
    P->>DB: record run + events
    alt ok
        P->>HB: ping success
    else degraded / aborted / crashed
        P->>AL: alert with reasons
        P->>HB: ping /fail
    end
    GH->>GH: commit pages + state (git history = content history)
    Note over HB: If no ping arrives at all (workflow disabled,<br/>secret expired), the heartbeat service alerts.
```

## Where each box lives in the code

| Box | File |
|---|---|
| Ingest | `src/pseo/keywords.py` |
| Cluster | `src/pseo/cluster.py` |
| Plan, Write, Editor | `src/pseo/agents.py` + `prompts/` |
| Rule + similarity checks | `src/pseo/quality.py`, `src/pseo/similarity.py` |
| Loop, sweep, circuit breaker, hashing | `src/pseo/pipeline.py` |
| Render | `src/pseo/render.py` + `templates/` |
| Report | `src/pseo/report.py` |
| State | `src/pseo/store.py` |
| Retries, timeouts, model fallback | `src/pseo/llm.py` |
| Lock, alerts, heartbeat | `src/pseo/ops.py` |
| Schedule | `.github/workflows/pseo.yml` |
