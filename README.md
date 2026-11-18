# Gem-bot

A small self-hosted bot that watches Indian government tender portals for one region every morning. For each new bid it pulls out the key facts, checks whether a given firm can realistically qualify, and sends a short Telegram digest.

It gives advice only. It never logs in, bids, pays, signs or messages anyone. Everything is public pages + PDFs + rules.

## What it does
- **Collects** new tenders each morning from public listing pages (rate-limited, identifies itself, respects `robots.txt`; never bypasses captchas).
- **Reads** the tender PDF (`pdftotext`, OCR fallback) and extracts EMD, ePBG, end date, MSE / startup relaxation, OEM authorisation, turnover and experience criteria, consignee location, and goods vs service vs works.
- **Rates** each bid per firm **GREEN / AMBER / RED** with a deterministic rules engine, e.g. "trader on a goods bid pays EMD", "works tender needs completed similar work orders", "drug / fuel / scrap needs a licence".
- **Shows its work:** every fact comes with the page and line it was read from, or says `not stated`. No guessing.
- **Sends** one Telegram digest plus one card per bid. Source outages are reported, never skipped silently.

AI is optional. Bid PDFs follow fixed templates, so static parsing does most of the work. An LLM (Gemini) only fills fields a free-form PDF hides in prose, and the rules engine always has the final say.

## Stack
Python 3 · Supabase (Postgres) · Telegram Bot API · systemd timer on a Raspberry Pi 4 · optional Gemini

## Roadmap
- [ ] **Phase 1:** first source end to end: scrape, parse PDF, rules, Telegram digest
- [ ] **Phase 2:** forward a bid PDF or link to the bot for an instant analysis; `/today` `/closing` `/watching`
- [ ] **Phase 3:** more regional sources, each with a feasibility note (ok / blocked / needs-login)
- [ ] **Phase 4:** weekly "what gets bought often" report

## Setup
```bash
cp .env.example .env                                   # fill in your own keys
cp config/entities.example.yaml config/entities.yaml   # your firms' facts (kept out of git)
```
Secrets live only in `.env`. Firm details live only in `config/entities.yaml`. Both are gitignored.
