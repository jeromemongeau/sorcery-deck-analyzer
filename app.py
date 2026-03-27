#!/usr/bin/env python3
"""
Sorcery Deck Analyzer — Interactive Web Playground
Flask app serving the threshold calculator + deck stats UI.

Run:  python Sorcery/app.py
Open: http://localhost:5055
"""

import json
import os
import re
import sys

import requests
from flask import Flask, jsonify, request, send_from_directory

# Ensure proper encoding on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from sorcery_threshold import (
    ARCHETYPE_ASSUMPTIONS,
    ELEMENTS,
    SITES_SEEN,
    CardDB,
    DeckAnalyzer,
    find_min_sources,
    generate_threshold_table,
    get_sites_seen,
    threshold_probability,
)

app = Flask(__name__, static_folder="static")
db = CardDB()


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/cards", methods=["GET"])
def get_cards():
    """Return all card names for autocomplete."""
    return jsonify(db.all_names())


@app.route("/api/card/<name>", methods=["GET"])
def get_card(name):
    """Look up a single card."""
    card, matched = db.get(name)
    if card:
        return jsonify({"name": matched, "data": card["guardian"],
                        "elements": card.get("elements", ""),
                        "subTypes": card.get("subTypes", "")})
    return jsonify({"error": f"Card not found: {name}"}), 404


def parse_curiosa_text(text: str) -> dict:
    """Parse curiosa.io deck page text into a deck dict.

    Curiosa.io scraped text has this structure per section:
        Avatar            <- section header
        (                 <- open paren
        1                 <- card count in section
        )                 <- close paren
        1                 <- quantity
        Imposter          <- card name
        [cost line or next qty]

    For spells: qty, name, cost repeat.
    For sites: qty, name repeat (no cost).
    Parsing stops at "Collection" or "Deck History".
    """
    lines = [l.strip() for l in text.strip().split("\n")]
    deck = {"avatar": [], "spellbook": [], "atlas": []}

    SECTION_MAP = {
        "avatar": "avatar",
        "aura": "spellbook",
        "artifact": "spellbook",
        "minion": "spellbook",
        "magic": "spellbook",
        "site": "atlas",
    }
    SECTION_NAMES = set(SECTION_MAP.keys())
    STOP_WORDS = {"collection", "deck history", "incomplete", "comments"}

    section = None
    i = 0
    while i < len(lines):
        line = lines[i]
        ll = line.lower()

        # Stop parsing at footer sections
        if any(ll.startswith(sw) for sw in STOP_WORDS):
            break

        # Detect section header (single word matching a section name)
        if ll in SECTION_NAMES:
            section = ll
            # Skip the "( count )" lines that follow
            i += 1
            while i < len(lines) and lines[i] in ("(", ")", "") or (lines[i].isdigit() and i + 1 < len(lines) and lines[i + 1] in (")", "")):
                i += 1
            continue

        if section is None:
            i += 1
            continue

        # Try to read a card: qty line, then name line, then optional cost line
        if line.isdigit():
            qty = int(line)
            if i + 1 < len(lines):
                name_line = lines[i + 1]
                # Validate: name should not be a section header, "(", ")", or stop word
                if (name_line.lower() not in SECTION_NAMES
                        and name_line not in ("(", ")")
                        and not any(name_line.lower().startswith(sw) for sw in STOP_WORDS)
                        and not name_line.isdigit()):
                    target = SECTION_MAP.get(section, "spellbook")
                    deck[target].append([name_line, qty])

                    # For spells (not sites): skip the cost line after the name
                    if section != "site" and i + 2 < len(lines) and lines[i + 2].isdigit():
                        i += 3  # skip qty + name + cost
                    else:
                        i += 2  # skip qty + name
                    continue
            i += 1
            continue

        # Handle "QTY CARD_NAME" on one line (manual text format)
        one_line = re.match(r"^(\d+)\s+(.+)$", line)
        if one_line and section:
            qty = int(one_line.group(1))
            card_name = one_line.group(2).strip()
            target = SECTION_MAP.get(section, "spellbook")
            deck[target].append([card_name, qty])
            i += 1
            continue

        i += 1

    return deck


def fetch_curiosa_deck(url: str) -> dict:
    """Fetch and parse a curiosa.io deck URL."""
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    # Extract text content — curiosa pages are JS-rendered, but the
    # text content from scrapling gives us the structured list
    # For the API, we'll use the MCP scrapling via a simpler approach:
    # just return the raw text for client-side fetching
    return resp.text


@app.route("/api/fetch-deck", methods=["POST"])
def fetch_deck():
    """Fetch a curiosa.io deck URL via Scrapling MCP and return parsed deck JSON."""
    data = request.json
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if "curiosa.io/decks/" not in url:
        return jsonify({"error": "Only curiosa.io deck URLs are supported"}), 400

    try:
        from scrapling import StealthyFetcher
        fetcher = StealthyFetcher()
        response = fetcher.fetch(url, network_idle=True, wait=2000)
        page_text = response.get_all_text(separator="\n")

        deck = parse_curiosa_text(page_text)
        if deck.get("avatar") or deck.get("spellbook") or deck.get("atlas"):
            return jsonify({"deck": deck})

        return jsonify({"error": "Could not parse deck from page. Try pasting the deck list manually."}), 422

    except Exception as e:
        return jsonify({"error": f"Failed to fetch: {str(e)}"}), 500


@app.route("/api/parse-text", methods=["POST"])
def parse_text():
    """Parse raw curiosa.io page text into a deck dict."""
    data = request.json
    text = data.get("text", "")
    deck = parse_curiosa_text(text)
    return jsonify({"deck": deck})


@app.route("/api/analyze", methods=["POST"])
def analyze_deck():
    """Full deck analysis. Expects JSON body with deck + settings."""
    data = request.json
    deck = data.get("deck", {})
    archetype = data.get("archetype", "midrange")
    on_the_play = data.get("on_the_play", True)
    target_prob = data.get("target_prob", 0.90)
    draw_schedule = data.get("draw_schedule", None)

    current_turn = data.get("current_turn", 0)  # 0 = on curve
    adjustments = data.get("adjustments", None)

    analyzer = DeckAnalyzer(deck, db, archetype=archetype,
                            on_the_play=on_the_play, target_prob=target_prob,
                            draw_schedule=draw_schedule, current_turn=current_turn,
                            adjustments=adjustments)
    report = analyzer.full_report()
    return jsonify(report)


@app.route("/api/sample-hand", methods=["POST"])
def sample_hand():
    """Generate a random opening hand."""
    data = request.json
    deck = data.get("deck", {})
    analyzer = DeckAnalyzer(deck, db)
    hand = analyzer.sample_hand()
    return jsonify(hand)


@app.route("/api/quick", methods=["POST"])
def quick_calc():
    """Quick threshold lookup."""
    data = request.json
    atlas_size = data.get("atlas_size", 30)
    sources = data.get("sources", 15)
    pips = data.get("pips", 2)
    turn = data.get("turn", 3)
    archetype = data.get("archetype", "midrange")
    on_the_play = data.get("on_the_play", True)

    sites_seen = get_sites_seen(archetype, turn, on_the_play)
    prob = threshold_probability(atlas_size, sources, sites_seen, pips)
    min_src = find_min_sources(atlas_size, sites_seen, pips, 0.90)

    return jsonify({
        "probability": round(prob * 100, 1),
        "sites_seen": sites_seen,
        "min_sources_90": min_src,
    })


@app.route("/api/table", methods=["POST"])
def threshold_table():
    """Generate full threshold table for an archetype."""
    data = request.json
    archetype = data.get("archetype", "midrange")
    atlas_size = data.get("atlas_size", 30)
    on_the_play = data.get("on_the_play", True)

    table = generate_threshold_table(archetype, atlas_size, on_the_play)
    return jsonify(table)


@app.route("/api/archetypes", methods=["GET"])
def archetypes():
    """Return archetype info and sites-seen tables."""
    return jsonify({
        "archetypes": ARCHETYPE_ASSUMPTIONS,
        "sites_seen": {k: {str(t): s for t, s in v.items()} for k, v in SITES_SEEN.items()},
    })


if __name__ == "__main__":
    os.makedirs(os.path.join(os.path.dirname(__file__), "static"), exist_ok=True)
    port = int(os.environ.get("PORT", 5055))
    print(f"Sorcery Deck Analyzer — http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
