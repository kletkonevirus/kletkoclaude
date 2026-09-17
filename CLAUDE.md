# Performance-Marketing AI Stack

This repository is my personal AI stack for running paid user acquisition at Choco
(food delivery inside a super-app). It is how I turn AI from "a chat I prompt" into
**systems that do real work** — a team of specialized agents, an operational ad
controller, and a self-running competitor scraper.

> **Note on this public version.** This is a sanitized copy for sharing. Internal
> data-tool names are replaced with generic ones (`get_data_context`, `data_marketing`,
> etc.), the courier dataset is **synthetic**, and no credentials are included
> (only `.env.example` with empty placeholders). The methodology and code are real.

---

## 1. `agents/` — a performance team as Claude agents

Instead of prompting Claude ad-hoc, I built **6 specialized agents**, each with its own
role and instruction file. Together they run my creative pipeline end to end. Each agent
is wired to our internal data layer, so it reasons on real numbers, not guesses.

| Agent | Role |
|---|---|
| `audience-insight-analyst` | Finds *why* and *when* people order (Jobs-to-be-Done), turns behavior into human insights |
| `ad-creative-director` | Turns insights into concepts, hooks and test variations — treats creative as targeting |
| `ad-copy-communicator` | Makes the message clear and instantly understood (KZ/RU), holds brand voice |
| `creative-performance-analyst` | Closes the learning loop: *why* a creative won (hook → CTR → CVR), feeds it back |
| `digital-ads-strategist` | Campaign structure, budget split, attribution/incrementality, channel-agnostic |
| `market-intel-researcher` | Competitor and seasonal context; strictly separates external web vs. internal data |

**The flow:** insight → concept → clear copy → launch → post-launch analysis → back to
insight. This is what keeps the creative backlog planned months ahead, while I stay the
only marketer on the paid stack.

These `.md` files are my main "instruction files" (the equivalent of a system prompt /
`agent.md` for each role).

## 2. `adpilot/` — operational ad controller (Python)

A tool that manages campaigns automatically instead of me watching dashboards. Working
prototype (runs on mock platform data; a live Meta adapter is ready). Two design ideas
make it different:

- **Spend is gated by real delivery capacity.** In food delivery, spending more is
  useless if couriers/kitchen can't deliver. AdPilot reads courier load and automatically
  throttles or pauses spend in an overloaded city, then restores it when capacity frees up.
- **Asymmetric autonomy.** Actions that *lower* risk (pause a loser, CPA stop-loss, cut
  budget) run automatically via **deterministic code — no LLM, fully auditable**. Actions
  that *raise* spend (scale a winner) never run alone — they go to a **human approval
  queue**. Money-touching decisions are safe by design.

See `adpilot/README.md` for architecture and the roadmap to production.

## 3. `competitor-monitor/` — self-running competitor scraper

A scheduled pipeline that needs zero manual effort: **GitHub Actions runs it every day
at 06:00 Almaty**, a Puppeteer scraper collects competitor ads (Wolt, Glovo, Yandex Eda in
KZ) from Google Ads Transparency, and writes the result to Google Drive, where a dashboard
reads it. Built with Node.js + Puppeteer + Google Drive API. See `competitor-monitor/SETUP.md`.

## 4. `skills/` — reusable Claude skills

Task-specific skills I reuse across work: growth marketing, content strategy, analytics
tracking, launch strategy, churn prevention.

---

## Philosophy

- **Build, don't just prompt.** Every part here is a system that runs, not a one-off chat.
- **Deterministic where money is at stake; LLM where judgment helps.** AI advises and
  analyzes; hard rules and human approval guard the budget.
- **Everything ties back to data.** Agents refuse to invent numbers — they either pull real
  data or flag the assumption.
