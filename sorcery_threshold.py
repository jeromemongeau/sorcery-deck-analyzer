#!/usr/bin/env python3
"""
Sorcery: Contested Realm — Deck Analyzer & Threshold Calculator

Uses hypergeometric probability to calculate the likelihood of meeting
elemental threshold requirements on curve for any given deck.

Usage:
    python sorcery_threshold.py --json '<deck_json>' --archetype aggro
    python sorcery_threshold.py --interactive

Card data from curiosa_cards.json.
Math based on: https://scourgealters.wixsite.com/blog/post/deckbuilder-s-toolkit-building-reliable-decks-in-sorcery-contested-realm
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from difflib import get_close_matches
from statistics import mean, median

from scipy.stats import hypergeom

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CARD_DB_PATH = os.path.join(SCRIPT_DIR, "curiosa_cards.json")

ELEMENTS = ["air", "earth", "fire", "water"]
ELEMENT_ICONS = {"air": "\u2694\ufe0f", "earth": "\U0001f7e2", "fire": "\U0001f525", "water": "\U0001f4a7"}
ELEMENT_LETTERS = {"air": "A", "earth": "E", "fire": "F", "water": "W"}

CARD_TYPES = ["Avatar", "Minion", "Magic", "Aura", "Artifact", "Site"]

KEYWORDS = [
    "Spellcaster", "Airborne", "Lethal", "Voidwalk", "Ranged",
    "Burrow", "Stealth", "Genesis", "Charge", "Defender",
    "Deathtouch", "Immobile", "Submerge",
]

# Sites seen per turn by archetype (from article)
# Each entry: turn_number -> cumulative sites seen
SITES_SEEN = {
    "aggro": {
        # Mulligan 2 sites, draw site T2, then ~33% of turns
        1: 5, 2: 6, 3: 6, 4: 6, 5: 7, 6: 8, 7: 8,
    },
    "midrange": {
        # Mulligan 3 sites, draw site ~50% of turns
        1: 5, 2: 6, 3: 7, 4: 7, 5: 8, 6: 8, 7: 9, 8: 9, 9: 10,
    },
    "pathfinder": {
        # No opening hand, play top site each turn
        1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 6, 8: 7, 9: 8, 10: 9,
    },
}

ARCHETYPE_ASSUMPTIONS = {
    "aggro": {
        "description": "Aggro",
        "mulligan_sites": 2,
        "site_draw_pattern": "Draw site T2, then ~33% of draw steps",
        "notes": "Fast deck, tops out around 6-7 sites total",
    },
    "midrange": {
        "description": "Mid-range",
        "mulligan_sites": 3,
        "site_draw_pattern": "Draw site ~50% of draw steps",
        "notes": "Balanced deck, reaches 9-10 sites over the game",
    },
    "pathfinder": {
        "description": "Pathfinder",
        "mulligan_sites": 0,
        "site_draw_pattern": "Play top site each turn (Pathfinder ability)",
        "notes": "No opening hand sites, atlas has no duplicates",
    },
}

# Special card names for threshold adjustments
SPECIAL_AVATARS = {
    "Elementalist": "elementalist",
    "Pathfinder": "pathfinder",
    "Seer": "seer",
}

CORE_CARDS = {"Ruby Core", "Onyx Core", "Aquamarine Core", "Amethyst Core"}
CORE_ELEMENT = {
    "Ruby Core": "fire",
    "Onyx Core": "earth",
    "Aquamarine Core": "water",
    "Amethyst Core": "air",
}

# Special cards file for extended threshold logic
SPECIAL_CARDS_PATH = os.path.join(SCRIPT_DIR, "special_cards.json")

def load_special_cards() -> dict:
    """Load the special cards config."""
    if os.path.exists(SPECIAL_CARDS_PATH):
        with open(SPECIAL_CARDS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

SPECIAL_CARDS = load_special_cards()

# Build lookup for special sites that provide threshold via abilities
SPECIAL_SITE_THRESHOLD = {}
for name, info in SPECIAL_CARDS.get("sites", {}).items():
    SPECIAL_SITE_THRESHOLD[name] = info

# Build lookup for spellbook sources beyond cores
SPECIAL_SPELLBOOK_SOURCES = {}
for name, info in SPECIAL_CARDS.get("spellbook_sources", {}).items():
    SPECIAL_SPELLBOOK_SOURCES[name] = info


# ---------------------------------------------------------------------------
# Card Database
# ---------------------------------------------------------------------------

class CardDB:
    """Load and query the curiosa card database."""

    def __init__(self, path: str = CARD_DB_PATH):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self._cards = {}
        self._names = []
        for card in raw:
            name = card["name"]
            self._cards[name.lower()] = card
            self._names.append(name)

    def get(self, name: str):
        """Look up a card by exact or fuzzy name. Returns (card_dict, matched_name) or (None, None)."""
        key = name.strip().lower()
        if key in self._cards:
            return self._cards[key], self._cards[key]["name"]
        # Fuzzy match
        matches = get_close_matches(key, list(self._cards.keys()), n=1, cutoff=0.7)
        if matches:
            return self._cards[matches[0]], self._cards[matches[0]]["name"]
        return None, None

    def all_names(self):
        return list(self._names)


# ---------------------------------------------------------------------------
# Hypergeometric Threshold Calculator
# ---------------------------------------------------------------------------

def threshold_probability(
    atlas_size: int,
    sources: int | float,
    sites_seen: int,
    pips_needed: int,
) -> float:
    """
    Calculate probability of having >= pips_needed sources of an element
    after seeing sites_seen cards from an atlas of atlas_size with `sources`
    matching sites.

    Uses hypergeometric distribution:
        P(X >= k) = 1 - hypergeom.cdf(k-1, N, K, n)

    NOTE: This treats each pip as a separate "ball". For multi-pip sites,
    use threshold_probability_multi_pip() instead.
    """
    if pips_needed <= 0:
        return 1.0
    # Clamp sources to integer for hypergeom
    src = int(round(sources))
    src = max(0, min(src, atlas_size))
    sites = min(sites_seen, atlas_size)
    if sites <= 0 or src <= 0:
        return 0.0
    return float(1 - hypergeom.cdf(pips_needed - 1, atlas_size, src, sites))


def _log_comb(n: int, k: int) -> float:
    """Log of binomial coefficient C(n, k) using lgamma for numerical stability."""
    if k < 0 or k > n:
        return float('-inf')
    if k == 0 or k == n:
        return 0.0
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def threshold_probability_multi_pip(
    atlas_size: int,
    K1: int,
    K2: int,
    sites_seen: int,
    pips_needed: int,
) -> float:
    """
    Accurate threshold probability using multivariate hypergeometric.

    Treats each CARD as a draw unit (not each pip). A 2-pip site satisfies
    a 2-pip requirement in a single draw.

    Args:
        atlas_size: N, total cards in atlas
        K1: number of cards with exactly 1 pip of the element
        K2: number of cards with exactly 2 pips of the element
        sites_seen: n, cards drawn
        pips_needed: k, total pips needed

    Returns: P(total pips from drawn cards >= k)
    """
    if pips_needed <= 0:
        return 1.0

    N = atlas_size
    n = min(sites_seen, N)
    k = pips_needed
    K1 = max(0, min(K1, N))
    K2 = max(0, min(K2, N - K1))
    K0 = N - K1 - K2

    if n <= 0:
        return 0.0
    if K1 + K2 == 0:
        return 0.0

    log_total = _log_comb(N, n)

    # Sum P(total pips < k) by enumerating (j1, j2) draws from 1-pip and 2-pip categories
    p_less = 0.0
    for j2 in range(min(K2, n) + 1):
        for j1 in range(min(K1, n - j2) + 1):
            if j1 + 2 * j2 >= k:
                break  # j1 only increases, so all further j1 values also >= k
            j0 = n - j1 - j2
            if j0 < 0 or j0 > K0:
                continue
            log_p = (_log_comb(K1, j1) + _log_comb(K2, j2) +
                     _log_comb(K0, j0) - log_total)
            p_less += math.exp(log_p)

    return max(0.0, min(1.0, 1.0 - p_less))


def find_min_sources(
    atlas_size: int,
    sites_seen: int,
    pips_needed: int,
    target_prob: float = 0.90,
) -> int:
    """Find the minimum number of sources needed to meet target probability."""
    for s in range(1, atlas_size + 1):
        if threshold_probability(atlas_size, s, sites_seen, pips_needed) >= target_prob:
            return s
    return atlas_size


def get_sites_seen(archetype: str, turn: int, on_the_play: bool = True,
                   draw_schedule: dict = None) -> int:
    """Get cumulative sites seen by a given turn.

    If draw_schedule is provided, it overrides the archetype table.
    draw_schedule format: {turn_number: "atlas"|"spellbook"|"skip", ...}
    plus optional "mulligan_sites": int (default 3 for opening hand).
    """
    if draw_schedule:
        # Opening hand: 3 atlas cards always
        opening = draw_schedule.get("opening_sites", 3)
        mulligan = draw_schedule.get("mulligan_sites", 0)
        sites = opening + mulligan

        for t in range(1, turn + 1):
            draw = draw_schedule.get(str(t), draw_schedule.get(t, ""))
            if draw == "none" or draw == "":
                continue  # no draw this turn
            if draw == "atlas":
                sites += 1
        return sites

    table = SITES_SEEN.get(archetype, SITES_SEEN["midrange"])
    max_turn = max(table.keys())
    t = min(turn, max_turn)
    # Find the closest turn <= t
    sites = 0
    for k in sorted(table.keys()):
        if k <= t:
            sites = table[k]
    # On the draw: +1 card seen (extra draw)
    if not on_the_play:
        sites += 1
    return sites


def get_spells_seen(turn: int, on_the_play: bool = True,
                    draw_schedule: dict = None) -> int:
    """Get cumulative spells seen by a given turn from draw schedule."""
    # Opening hand: 3 spellbook cards always
    opening = 3
    mulligan_spells = 0
    if draw_schedule:
        mulligan_spells = draw_schedule.get("mulligan_spells", 0)

    spells = opening + mulligan_spells

    for t in range(1, turn + 1):
        if draw_schedule:
            draw = draw_schedule.get(str(t), draw_schedule.get(t, ""))
            if draw == "none" or draw == "":
                continue
            if draw == "spellbook":
                spells += 1
        else:
            if t == 1 and on_the_play:
                continue
            # Default: assume ~50% spellbook draws
            spells += 1  # rough estimate
    return spells


def combined_threshold_probability(
    atlas_size: int,
    atlas_K1: int,
    atlas_K2: int,
    sites_seen: int,
    pips_needed: int,
    spellbook_size: int = 60,
    spellbook_sources: int = 0,
    spells_seen: int = 0,
) -> float:
    """Calculate probability of meeting threshold from atlas + spellbook sources combined.

    Atlas uses the multi-pip model (K1=1-pip cards, K2=2-pip cards).
    Spellbook sources always provide 1 pip each (standard hypergeometric).

    For each possible spellbook contribution (0..min(sb_sources, sb_seen)):
    P(exactly j from spellbook) * P(at least k-j from atlas using multi-pip model).
    """
    if pips_needed <= 0:
        return 1.0
    if spellbook_sources <= 0 or spells_seen <= 0:
        return threshold_probability_multi_pip(atlas_size, atlas_K1, atlas_K2, sites_seen, pips_needed)

    src_spell = max(0, min(spellbook_sources, spellbook_size))
    n_spell = min(spells_seen, spellbook_size)

    if sites_seen <= 0:
        return 0.0

    total_prob = 0.0
    for from_spell in range(min(src_spell, n_spell) + 1):
        p_spell = float(hypergeom.pmf(from_spell, spellbook_size, src_spell, n_spell))
        needed_from_atlas = max(0, pips_needed - from_spell)
        p_atlas = threshold_probability_multi_pip(atlas_size, atlas_K1, atlas_K2, sites_seen, needed_from_atlas)
        total_prob += p_spell * p_atlas

    return min(1.0, total_prob)


def earliest_castable_turn(mana_cost: int, archetype: str) -> int:
    """Determine the earliest turn a spell with given mana cost can be cast.

    Turn number = mana cost, since you get 1 site/mana per turn.
    Minimum turn 1.
    """
    return max(1, mana_cost)


# ---------------------------------------------------------------------------
# Deck Analysis
# ---------------------------------------------------------------------------

class DeckAnalyzer:
    """Analyze a Sorcery deck for statistics and threshold requirements."""

    def __init__(self, deck: dict, db: CardDB, archetype: str = "midrange",
                 on_the_play: bool = True, target_prob: float = 0.90,
                 draw_schedule: dict = None, current_turn: int = 0,
                 adjustments: dict = None):
        self.db = db
        self.archetype = archetype.lower().replace("-", "").replace(" ", "")
        if self.archetype in ("mid", "mid-range", "mid_range"):
            self.archetype = "midrange"
        self.draw_schedule = draw_schedule
        self.current_turn = current_turn  # 0 = on curve, N = force all spells to turn N
        # Manual adjustments
        self.adj_extra_sites = 0
        self.adj_extra_spells = 0
        self.adj_extra_pips = {}
        if adjustments:
            self.adj_extra_sites = adjustments.get("extra_sites", 0)
            self.adj_extra_spells = adjustments.get("extra_spells", 0)
            self.adj_extra_pips = adjustments.get("extra_pips", {})
        self.on_the_play = on_the_play
        self.target_prob = target_prob

        # Parse deck
        self.avatar = None
        self.avatar_name = None
        self.spellbook = []  # list of (card_data, matched_name, qty)
        self.atlas = []      # list of (card_data, matched_name, qty)
        self.unmatched = []   # cards not found in DB

        self._parse_deck(deck)

        # Derived
        self.atlas_size = sum(qty for _, _, qty in self.atlas)
        self.spellbook_size = sum(qty for _, _, qty in self.spellbook)

        # Check for special avatar
        self.is_elementalist = False
        self.is_pathfinder = False
        self.is_seer = False
        if self.avatar:
            aname = self.avatar["name"]
            if aname == "Elementalist":
                self.is_elementalist = True
            if aname == "Pathfinder":
                self.is_pathfinder = True
                self.archetype = "pathfinder"
            if aname == "Seer":
                self.is_seer = True

        # Count sites-seen bonus from spellbook cards that draw/play sites
        self.adj_extra_sites += self._count_sites_seen_bonus()

    def _count_sites_seen_bonus(self) -> float:
        """Count extra sites seen from spellbook cards that draw/play sites.

        Uses hypergeometric probability of having drawn at least 1 copy
        by a mid-game turn (~5 spells seen from a spellbook).
        """
        MID_GAME_SPELLS_SEEN = 5  # ~turn 3 for midrange

        bonus = 0.0
        for card, name, qty in self.spellbook:
            special = SPECIAL_SPELLBOOK_SOURCES.get(name)
            if not special or "sites_seen_trigger" not in special:
                continue
            # P(drawn at least 1 copy by mid-game)
            p_drawn = 1 - float(hypergeom.cdf(0, self.spellbook_size, qty,
                                               min(MID_GAME_SPELLS_SEEN, self.spellbook_size)))
            bonus += p_drawn
        return bonus

    def _get_sites_seen_breakdown(self) -> list:
        """Return per-card breakdown of sites_seen_bonus for display."""
        MID_GAME_SPELLS_SEEN = 5
        result = []
        for card, name, qty in self.spellbook:
            special = SPECIAL_SPELLBOOK_SOURCES.get(name)
            if not special or "sites_seen_trigger" not in special:
                continue
            p_drawn = 1 - float(hypergeom.cdf(0, self.spellbook_size, qty,
                                               min(MID_GAME_SPELLS_SEEN, self.spellbook_size)))
            result.append({
                "name": name,
                "qty": qty,
                "p_drawn": round(p_drawn * 100, 1),
                "bonus": round(p_drawn, 2),
            })
        return result

    def _parse_deck(self, deck: dict):
        # Avatar
        for name, qty in deck.get("avatar", []):
            card, matched = self.db.get(name)
            if card:
                self.avatar = card
                self.avatar_name = matched
            else:
                self.unmatched.append(("avatar", name, qty))

        # Spellbook (all non-site cards)
        for name, qty in deck.get("spellbook", []):
            card, matched = self.db.get(name)
            if card:
                self.spellbook.append((card, matched, qty))
            else:
                self.unmatched.append(("spellbook", name, qty))

        # Atlas (sites)
        for name, qty in deck.get("atlas", []):
            card, matched = self.db.get(name)
            if card:
                self.atlas.append((card, matched, qty))
            else:
                self.unmatched.append(("atlas", name, qty))

    # --- Statistics ---

    def overview(self) -> dict:
        """Basic deck overview."""
        result = {
            "avatar": self.avatar_name,
            "avatar_ability": "",
            "spellbook_count": self.spellbook_size,
            "atlas_count": self.atlas_size,
            "total_count": self.spellbook_size + self.atlas_size + (1 if self.avatar else 0),
        }
        if self.avatar:
            rules = self.avatar["guardian"].get("rulesText", "") or ""
            # Get the ability part (after "Tap →" line)
            lines = rules.replace("\r\n", "\n").split("\n")
            ability_lines = [l.strip() for l in lines if l.strip() and "Tap →" not in l.lower() and "tap → play or draw" not in l.lower()]
            result["avatar_ability"] = " | ".join(ability_lines) if ability_lines else rules[:120]
        return result

    def mana_curve(self) -> dict:
        """Mana cost distribution for spellbook cards."""
        curve = Counter()
        costs = []
        for card, name, qty in self.spellbook:
            cost = card["guardian"].get("cost")
            if cost is not None:
                curve[cost] += qty
                costs.extend([cost] * qty)
        return {
            "distribution": dict(sorted(curve.items())),
            "average": round(mean(costs), 2) if costs else 0,
            "median": median(costs) if costs else 0,
        }

    def type_breakdown(self) -> dict:
        """Card type counts and minion subtype breakdown."""
        types = Counter()
        subtypes = Counter()
        for card, name, qty in self.spellbook:
            ctype = card["guardian"]["type"]
            types[ctype] += qty
            if ctype == "Minion":
                subs = card.get("subTypes", "") or ""
                for sub in subs.split(", "):
                    sub = sub.strip()
                    if sub:
                        subtypes[sub] += qty
        return {
            "types": dict(types.most_common()),
            "minion_subtypes": dict(subtypes.most_common()),
        }

    def element_distribution(self) -> dict:
        """Element distribution in spellbook and atlas."""
        # Spellbook elements
        spell_elements = Counter()
        for card, name, qty in self.spellbook:
            elems = card.get("elements", "") or ""
            for e in elems.split(", "):
                e = e.strip().lower()
                if e and e != "none":
                    spell_elements[e] += qty

        # Atlas sources per element (counting actual pip values + special sites)
        atlas_sources = {e: 0 for e in ELEMENTS}
        multi_sites = []
        special_sites_list = []
        for card, name, qty in self.atlas:
            th = card["guardian"].get("thresholds", {})
            providing = [e for e in ELEMENTS if th.get(e, 0) > 0]

            # Check for special site with ability-based threshold
            special = SPECIAL_SITE_THRESHOLD.get(name)
            if special and not providing:
                sp_provides = special.get("provides", [])
                condition = special.get("condition", "")
                if isinstance(sp_provides, list) and sp_provides:
                    if condition in ("pay_mana", "innately_flooded", "conditional_threshold"):
                        # Annual Fair, Mismanaged Mortuary, City of X: count as sources
                        for e in sp_provides:
                            atlas_sources[e] += qty * special.get("pips_each", 1)
                        special_sites_list.append((name, sp_provides, qty, condition))
                    else:
                        # Valley of Delight, Mirror Realm, Blooms, etc: show as special
                        special_sites_list.append((name, sp_provides, qty, condition))

            for e in providing:
                atlas_sources[e] += qty * th.get(e, 0)
            if len(providing) > 1:
                multi_sites.append((name, providing, qty))

        return {
            "spellbook_elements": dict(spell_elements.most_common()),
            "atlas_sources": {e: v for e, v in atlas_sources.items() if v > 0},
            "multi_element_sites": multi_sites,
            "special_sites": special_sites_list,
        }

    def keyword_census(self) -> dict:
        """Count keywords across spellbook cards."""
        kw_count = Counter()
        for card, name, qty in self.spellbook:
            rules = card["guardian"].get("rulesText", "") or ""
            for kw in KEYWORDS:
                if kw.lower() in rules.lower():
                    kw_count[kw] += qty
        return dict(kw_count.most_common())

    def combat_stats(self) -> dict:
        """Attack/defence statistics for minions."""
        attacks = []
        defences = []
        for card, name, qty in self.spellbook:
            if card["guardian"]["type"] == "Minion":
                atk = card["guardian"].get("attack")
                dfn = card["guardian"].get("defence")
                if atk is not None:
                    attacks.extend([atk] * qty)
                if dfn is not None:
                    defences.extend([dfn] * qty)
        result = {}
        if attacks:
            result["avg_attack"] = round(mean(attacks), 2)
            result["median_attack"] = median(attacks)
            result["attack_distribution"] = dict(Counter(attacks).most_common())
        if defences:
            result["avg_defence"] = round(mean(defences), 2)
            result["median_defence"] = median(defences)
            result["defence_distribution"] = dict(Counter(defences).most_common())
        return result

    def rarity_breakdown(self) -> dict:
        """Rarity distribution across the full deck."""
        rarities = Counter()
        for card, name, qty in self.spellbook + self.atlas:
            rarity = card["guardian"].get("rarity", "Unknown")
            rarities[rarity] += qty
        return dict(rarities.most_common())

    # --- New Stat Sections ---

    def deck_composition(self) -> dict:
        """Deck composition metrics."""
        total = self.spellbook_size
        types = Counter()
        for card, name, qty in self.spellbook:
            types[card["guardian"]["type"]] += qty

        permanents = types.get("Minion", 0) + types.get("Artifact", 0) + types.get("Aura", 0)
        non_permanents = types.get("Magic", 0)
        minions = types.get("Minion", 0)
        spells_non_minion = total - minions

        # Unique cards and copies
        unique_cards = len(self.spellbook)
        singletons = sum(1 for _, _, qty in self.spellbook if qty == 1)
        avg_copies = round(total / unique_cards, 2) if unique_cards else 0

        return {
            "permanent_count": permanents,
            "non_permanent_count": non_permanents,
            "minion_to_spell_ratio": f"{minions}:{spells_non_minion}",
            "unique_cards": unique_cards,
            "singletons": singletons,
            "avg_copies": avg_copies,
        }

    def mana_curve_extended(self) -> dict:
        """Extended mana curve analysis."""
        by_type = defaultdict(lambda: Counter())
        costs = []
        for card, name, qty in self.spellbook:
            cost = card["guardian"].get("cost")
            ctype = card["guardian"]["type"]
            if cost is not None:
                by_type[ctype][cost] += qty
                costs.extend([cost] * qty)

        low = sum(1 for c in costs if c <= 2)
        mid = sum(1 for c in costs if 3 <= c <= 4)
        high = sum(1 for c in costs if c >= 5)
        total = len(costs) or 1

        # Turn-1 playable: cost 1, threshold <= 1 pip total
        t1_playable = 0
        for card, name, qty in self.spellbook:
            cost = card["guardian"].get("cost")
            th = card["guardian"].get("thresholds", {})
            total_pips = sum(th.values())
            if cost is not None and cost <= 1 and total_pips <= 1:
                t1_playable += qty

        return {
            "by_type": {t: dict(sorted(c.items())) for t, c in by_type.items()},
            "low_mid_high": {"low_0_2": low, "mid_3_4": mid, "high_5_plus": high,
                             "low_pct": round(low/total*100), "mid_pct": round(mid/total*100),
                             "high_pct": round(high/total*100)},
            "turn_1_playable": t1_playable,
        }

    def combat_extended(self) -> dict:
        """Extended combat analysis."""
        minions = []
        for card, name, qty in self.spellbook:
            if card["guardian"]["type"] != "Minion":
                continue
            atk = card["guardian"].get("attack")
            dfn = card["guardian"].get("defence")
            cost = card["guardian"].get("cost") or 1
            if atk is not None and dfn is not None:
                for _ in range(qty):
                    minions.append({"atk": atk, "dfn": dfn, "cost": cost, "name": name})

        if not minions:
            return {}

        offensive = sum(1 for m in minions if m["atk"] > m["dfn"])
        defensive = sum(1 for m in minions if m["dfn"] > m["atk"])
        balanced = sum(1 for m in minions if m["atk"] == m["dfn"])
        zero_atk = sum(1 for m in minions if m["atk"] == 0)
        max_atk = max(m["atk"] for m in minions)
        ratios = [round(m["atk"] / m["cost"], 2) for m in minions if m["cost"] > 0]
        efficiency = [round((m["atk"] + m["dfn"]) / m["cost"], 2) for m in minions if m["cost"] > 0]

        return {
            "offensive": offensive,
            "defensive": defensive,
            "balanced": balanced,
            "zero_attack": zero_atk,
            "max_attack": max_atk,
            "avg_power_to_cost": round(mean(ratios), 2) if ratios else 0,
            "avg_efficiency": round(mean(efficiency), 2) if efficiency else 0,
        }

    def element_extended(self) -> dict:
        """Extended element analysis."""
        elements_used = set()
        multi_element_cards = 0
        total_pips = 0
        card_count = 0
        heaviest_card = None
        heaviest_pips = 0

        for card, name, qty in self.spellbook:
            th = card["guardian"].get("thresholds", {})
            pips = sum(th.values())
            active_elems = [e for e in ELEMENTS if th.get(e, 0) > 0]
            if pips > 0:
                card_count += qty
                total_pips += pips * qty
                elements_used.update(active_elems)
                if len(active_elems) > 1:
                    multi_element_cards += qty
                if pips > heaviest_pips:
                    heaviest_pips = pips
                    heaviest_card = name

        return {
            "element_count": len(elements_used),
            "element_label": ["", "Mono", "Dual", "Tri", "Quad"][min(len(elements_used), 4)],
            "multi_element_cards": multi_element_cards,
            "threshold_intensity": round(total_pips / card_count, 2) if card_count else 0,
            "heaviest_card": heaviest_card,
            "heaviest_pips": heaviest_pips,
        }

    def spatial_movement(self) -> dict:
        """Spatial & movement analysis (unique to Sorcery)."""
        evasion = Counter()
        mobility = {"ground": 0, "air": 0, "underground": 0, "aquatic": 0}
        immobile = 0
        ranged = 0
        positional = 0

        for card, name, qty in self.spellbook:
            rules = (card["guardian"].get("rulesText") or "").lower()
            ctype = card["guardian"]["type"]

            if ctype == "Minion":
                has_evasion = False
                if "airborne" in rules:
                    evasion["Airborne"] += qty; mobility["air"] += qty; has_evasion = True
                if "burrow" in rules:
                    evasion["Burrow"] += qty; mobility["underground"] += qty; has_evasion = True
                if "submerge" in rules:
                    evasion["Submerge"] += qty; mobility["aquatic"] += qty; has_evasion = True
                if "voidwalk" in rules:
                    evasion["Voidwalk"] += qty; has_evasion = True
                if "immobile" in rules:
                    immobile += qty
                if not has_evasion and "immobile" not in rules:
                    mobility["ground"] += qty

            if "ranged" in rules or "projectile" in rules:
                ranged += qty
            if "adjacent" in rules or "nearby" in rules:
                positional += qty

        total_minions = sum(qty for card, _, qty in self.spellbook if card["guardian"]["type"] == "Minion")
        evasion_pct = round(sum(evasion.values()) / total_minions * 100) if total_minions else 0

        return {
            "evasion": dict(evasion.most_common()),
            "evasion_pct": evasion_pct,
            "mobility": mobility,
            "immobile": immobile,
            "ranged": ranged,
            "positional_cards": positional,
        }

    def triggered_abilities(self) -> dict:
        """Triggered and activated ability counts."""
        genesis = 0; deathrite = 0; tap_ability = 0; passive = 0

        for card, name, qty in self.spellbook:
            rules = (card["guardian"].get("rulesText") or "").lower()
            if "genesis" in rules:
                genesis += qty
            if "deathrite" in rules:
                deathrite += qty
            if "tap →" in rules or "tap ->" in rules:
                tap_ability += qty
            # Passive: has rules but no trigger keyword
            if rules and "genesis" not in rules and "deathrite" not in rules and "tap" not in rules:
                if any(kw in rules for kw in ["provides", "has ", "your ", "whenever", "this site"]):
                    passive += qty

        return {
            "genesis": genesis,
            "deathrite": deathrite,
            "tap_ability": tap_ability,
            "passive": passive,
        }

    def effect_categories(self) -> dict:
        """Categorize spells by effect type."""
        cats = Counter()
        for card, name, qty in self.spellbook:
            rules = (card["guardian"].get("rulesText") or "").lower()
            if "draw a" in rules or "draw two" in rules:
                cats["Card Draw"] += qty
            if "destroy" in rules or "banish" in rules:
                cats["Removal"] += qty
            if "damage" in rules and ("deal" in rules or "takes" in rules):
                cats["Direct Damage"] += qty
            if any(w in rules for w in ["all minions", "all units", "all enemies", "each minion", "each enemy"]):
                cats["Board Wipe"] += qty
            if any(w in rules for w in ["power", "+1", "+2", "+3", "buff", "gets +"]):
                cats["Buff"] += qty
            if any(w in rules for w in ["heal", "gain life", "gains life", "restore"]):
                cats["Healing"] += qty
            if "token" in rules or "summon a" in rules:
                cats["Token Gen"] += qty
            if "cemetery" in rules and ("cast" in rules or "return" in rules or "summon" in rules):
                cats["Recursion"] += qty
            if "search your" in rules:
                cats["Tutor"] += qty
            if "flood" in rules:
                cats["Flood"] += qty
            if "ward" in rules:
                cats["Ward"] += qty

        return dict(cats.most_common())

    def spellcaster_coverage(self) -> dict:
        """Spellcaster analysis — critical Sorcery mechanic."""
        spellcasters = []
        total_minions = 0
        for card, name, qty in self.spellbook:
            if card["guardian"]["type"] != "Minion":
                continue
            total_minions += qty
            rules = (card["guardian"].get("rulesText") or "").lower()
            if "spellcaster" in rules:
                cost = card["guardian"].get("cost") or 0
                spellcasters.append({"name": name, "qty": qty, "cost": cost})

        total_sc = sum(s["qty"] for s in spellcasters)
        cost_curve = Counter()
        for s in spellcasters:
            cost_curve[s["cost"]] += s["qty"]

        return {
            "count": total_sc,
            "cards": spellcasters,
            "ratio": f"{total_sc}:{total_minions}" if total_minions else "0:0",
            "ratio_pct": round(total_sc / total_minions * 100) if total_minions else 0,
            "cost_curve": dict(sorted(cost_curve.items())),
        }

    def site_analysis(self) -> dict:
        """Detailed site/atlas analysis."""
        water_sites = 0; land_sites = 0
        genesis_sites = 0; ability_sites = 0
        subtypes = Counter()

        for card, name, qty in self.atlas:
            th = card["guardian"].get("thresholds", {})
            rules = (card["guardian"].get("rulesText") or "").lower()
            subs = card.get("subTypes", "") or ""

            # Water vs Land (water if has water threshold or is flooded)
            if th.get("water", 0) > 0 or "flooded" in rules or "flood" in rules:
                water_sites += qty
            else:
                land_sites += qty

            if "genesis" in rules:
                genesis_sites += qty
            if "→" in rules or "->" in rules:
                ability_sites += qty

            for s in subs.split(", "):
                s = s.strip()
                if s:
                    subtypes[s] += qty

        # Body of water synergy: submerge minions + water sites + flood cards
        submerge_count = 0
        flood_cards = 0
        for card, name, qty in self.spellbook:
            rules = (card["guardian"].get("rulesText") or "").lower()
            if "submerge" in rules:
                submerge_count += qty
            if "flood" in rules:
                flood_cards += qty

        total_sites = self.atlas_size or 1
        return {
            "water_sites": water_sites,
            "land_sites": land_sites,
            "water_pct": round(water_sites / total_sites * 100),
            "genesis_sites": genesis_sites,
            "ability_sites": ability_sites,
            "subtypes": dict(subtypes.most_common()) if subtypes else {},
            "water_synergy": {
                "water_sites": water_sites,
                "submerge_minions": submerge_count,
                "flood_cards": flood_cards,
                "score": water_sites + submerge_count + flood_cards,
            },
        }

    def archetype_indicators(self) -> dict:
        """Deck archetype scoring."""
        curve = self.mana_curve()
        avg_cost = curve.get("average", 3)
        ec = self.effect_categories()
        kw = self.keyword_census()

        total = self.spellbook_size or 1
        removal = ec.get("Removal", 0) + ec.get("Direct Damage", 0)
        board_wipes = ec.get("Board Wipe", 0)
        charge = kw.get("Charge", 0)
        low_cost = sum(qty for card, _, qty in self.spellbook if (card["guardian"].get("cost") or 99) <= 2)

        # Aggro: low curve, charge, burn
        aggro = min(100, int((4 - avg_cost) * 20 + charge * 5 + low_cost * 2))
        aggro = max(0, aggro)

        # Control: removal, board wipes, high curve
        control = min(100, int(removal * 3 + board_wipes * 10 + (avg_cost - 3) * 15))
        control = max(0, control)

        # Midrange: balanced
        midrange = max(0, 100 - abs(aggro - control))

        # Tempo: charge + low-cost removal
        low_removal = sum(qty for card, _, qty in self.spellbook
                          if (card["guardian"].get("cost") or 99) <= 3
                          and any(w in (card["guardian"].get("rulesText") or "").lower()
                                  for w in ["destroy", "banish", "damage"]))
        tempo = min(100, int(charge * 8 + low_removal * 5))

        return {
            "aggro": aggro, "control": control, "midrange": midrange, "tempo": tempo,
        }

    def sample_hand(self) -> dict:
        """Generate a random sample opening hand (3 spells + 3 sites)."""
        import random
        # Build pools
        spell_pool = []
        for card, name, qty in self.spellbook:
            spell_pool.extend([name] * qty)
        site_pool = []
        for card, name, qty in self.atlas:
            site_pool.extend([name] * qty)

        spells = random.sample(spell_pool, min(3, len(spell_pool))) if spell_pool else []
        sites = random.sample(site_pool, min(3, len(site_pool))) if site_pool else []

        return {"spells": spells, "sites": sites}

    def card_quality(self) -> dict:
        """Advanced card quality metrics."""
        total_atk = 0; total_cost = 0; total_stats = 0
        removal_count = 0; total_cards = self.spellbook_size or 1

        for card, name, qty in self.spellbook:
            cost = card["guardian"].get("cost") or 0
            atk = card["guardian"].get("attack") or 0
            dfn = card["guardian"].get("defence") or 0
            rules = (card["guardian"].get("rulesText") or "").lower()

            total_cost += cost * qty
            if card["guardian"]["type"] == "Minion":
                total_atk += atk * qty
                total_stats += (atk + dfn) * qty
            if "destroy" in rules or "banish" in rules or "damage" in rules:
                removal_count += qty

        return {
            "threat_density": round(total_atk / total_cost, 2) if total_cost else 0,
            "mana_efficiency": round(total_stats / total_cost, 2) if total_cost else 0,
            "interaction_density": round(removal_count / total_cards * 100),
        }

    def set_info(self) -> dict:
        """Set and collection info."""
        sets = Counter()
        artists = set()
        for card, name, qty in self.spellbook + self.atlas:
            for s in card.get("sets", []):
                sets[s["name"]] += qty
                for v in s.get("variants", []):
                    if v.get("artist"):
                        artists.add(v["artist"])
                break  # only count first set

        return {
            "sets": dict(sets.most_common()),
            "artist_count": len(artists),
        }

    def all_stats(self) -> dict:
        """Compute all deck statistics."""
        return {
            "overview": self.overview(),
            "mana_curve": self.mana_curve(),
            "mana_curve_extended": self.mana_curve_extended(),
            "type_breakdown": self.type_breakdown(),
            "element_distribution": self.element_distribution(),
            "element_extended": self.element_extended(),
            "keyword_census": self.keyword_census(),
            "combat_stats": self.combat_stats(),
            "combat_extended": self.combat_extended(),
            "rarity_breakdown": self.rarity_breakdown(),
            "deck_composition": self.deck_composition(),
            "spatial_movement": self.spatial_movement(),
            "triggered_abilities": self.triggered_abilities(),
            "effect_categories": self.effect_categories(),
            "spellcaster_coverage": self.spellcaster_coverage(),
            "site_analysis": self.site_analysis(),
            "archetype_indicators": self.archetype_indicators(),
            "card_quality": self.card_quality(),
            "set_info": self.set_info(),
        }

    # --- Threshold Analysis ---

    def _count_atlas_sources(self, element: str) -> tuple[int, list[str], int, int]:
        """Count total threshold pips for an element across the atlas.
        Sites like Active Volcano (fire: 2) count as 2 sources per copy.
        Also handles special sites (Annual Fair, Valley of Delight, etc.)
        that provide threshold through abilities rather than innate icons.
        Returns (total_pips, contributors, K1, K2) where:
          K1 = number of cards with exactly 1 pip of this element
          K2 = number of cards with exactly 2 pips of this element"""
        count = 0
        K1 = 0  # cards with 1 pip
        K2 = 0  # cards with 2 pips
        contributors = []
        for card, name, qty in self.atlas:
            th = card["guardian"].get("thresholds", {})
            pips = th.get(element, 0)

            # Check if this is a special site with ability-based threshold
            special = SPECIAL_SITE_THRESHOLD.get(name)
            if special and pips == 0:
                provides = special.get("provides", [])
                if isinstance(provides, list) and element in provides:
                    condition = special.get("condition", "")
                    pips_each = special.get("pips_each", 1)
                    label = f"{qty}x {name}" if qty > 1 else name

                    if condition == "pay_mana":
                        count += qty * pips_each
                        K1 += qty  # special sites always provide 1 pip each
                        label += " (costs 1 mana)"
                        contributors.append(label)
                    elif condition == "innately_flooded":
                        count += qty * pips_each
                        K1 += qty
                        label += " (flooded)"
                        contributors.append(label)
                    elif condition == "conditional_threshold":
                        count += qty * pips_each
                        K1 += qty
                        contributors.append(label)
                    elif condition in ("genesis_choose", "copy", "genesis_temporary", "scry_atlas"):
                        pass
                    continue

            if pips > 0:
                count += qty * pips
                if pips == 1:
                    K1 += qty
                elif pips >= 2:
                    K2 += qty
                pip_label = f"({pips} pips)" if pips > 1 else ""
                label = f"{qty}x {name}" if qty > 1 else name
                if pip_label:
                    label += f" {pip_label}"
                contributors.append(label)
        return count, contributors, K1, K2

    def _count_spellbook_sources(self, element: str) -> float:
        """Count fractional sources from spellbook cards that provide threshold.
        Uses special_cards.json config + hardcoded Cores as fallback."""
        source_count = self._count_spellbook_sources_exact(element)
        # Fractional credit per article
        if source_count == 0:
            return 0.0
        if source_count == 1:
            return 0.15
        if source_count == 2:
            return 0.20
        if source_count == 3:
            return 0.33
        if source_count <= 5:
            return 0.50
        return 0.75

    def _count_spellbook_sources_exact(self, element: str) -> int:
        """Count exact number of spellbook cards that provide threshold for an element.
        Returns total card count (not fractional)."""
        source_count = 0
        for card, name, qty in self.spellbook:
            special = SPECIAL_SPELLBOOK_SOURCES.get(name)
            if special:
                provides = special.get("provides", [])
                if element in provides and special.get("condition") == "in_play":
                    source_count += qty
                    continue
            if name in CORE_CARDS and CORE_ELEMENT.get(name) == element:
                source_count += qty
        return source_count

    def _get_special_site_adjustments(self, element: str, pips: int) -> float:
        """Calculate fractional adjustments from special sites like Valley of Delight, Floodplain."""
        adjustment = 0.0

        for card, name, qty in self.atlas:
            # Valley of Delight: always 0.5 per element (genesis choose one)
            if name == "Valley of Delight":
                adjustment += 0.5 * qty

            # Floodplain (water only, for 2+ pips)
            if name == "Floodplain" and element == "water" and pips >= 2:
                # Already counted as 1 source; add 0.33 extra
                adjustment += 0.33 * qty

        return adjustment

    def threshold_analysis(self) -> dict:
        """Full threshold analysis for the deck."""
        # Count sources per element (total pips + card breakdown)
        element_sources = {}
        element_contributors = {}
        element_K1 = {}  # cards with 1 pip per element
        element_K2 = {}  # cards with 2 pips per element
        for e in ELEMENTS:
            count, contribs, k1, k2 = self._count_atlas_sources(e)
            element_K1[e] = k1
            element_K2[e] = k2
            if count > 0:
                element_sources[e] = count
                element_contributors[e] = contribs

        # Analyze each unique spell's threshold requirements
        spell_analysis = []
        seen_spells = set()

        for card, name, qty in self.spellbook:
            if name in seen_spells:
                continue
            seen_spells.add(name)

            th = card["guardian"].get("thresholds", {})
            cost = card["guardian"].get("cost") or 0

            # Skip cards with no threshold requirements
            needed = {e: th.get(e, 0) for e in ELEMENTS if th.get(e, 0) > 0}
            if not needed:
                continue

            if self.current_turn > 0:
                turn = self.current_turn
            else:
                turn = earliest_castable_turn(cost, self.archetype)
            sites_seen = get_sites_seen(self.archetype, turn, self.on_the_play,
                                        self.draw_schedule)
            sites_seen = int(round(sites_seen + self.adj_extra_sites))
            spells_seen = get_spells_seen(turn, self.on_the_play, self.draw_schedule)
            spells_seen = int(round(spells_seen + self.adj_extra_spells))

            # Probability of having at least 1 copy of this spell in hand by this turn
            # Hypergeometric: P(X >= 1) from spellbook_size with qty copies after spells_seen draws
            if spells_seen > 0 and self.spellbook_size > 0:
                draw_prob = float(1 - hypergeom.cdf(0, self.spellbook_size, qty, min(spells_seen, self.spellbook_size)))
            else:
                draw_prob = 0.0

            # Per-element probability
            element_probs = {}
            element_details = {}
            for e, pips in needed.items():
                actual_pips = pips
                # Elementalist: reduce needed pips by 1
                if self.is_elementalist:
                    actual_pips = max(0, pips - 1)

                base_sources = element_sources.get(e, 0)
                special_adj = self._get_special_site_adjustments(e, pips)
                manual_pips = self.adj_extra_pips.get(e, 0)
                atlas_eff = base_sources + special_adj + manual_pips

                # K1/K2 for multi-pip model (add special adj as extra 1-pip cards)
                eff_K1 = element_K1.get(e, 0) + int(round(special_adj + manual_pips))
                eff_K2 = element_K2.get(e, 0)

                # Spellbook sources (exact count for combined probability)
                sb_exact = self._count_spellbook_sources_exact(e)
                sb_fractional = self._count_spellbook_sources(e)

                if sb_exact > 0:
                    # Use combined probability (atlas + spellbook)
                    prob = combined_threshold_probability(
                        self.atlas_size, eff_K1, eff_K2, sites_seen, actual_pips,
                        self.spellbook_size, sb_exact, spells_seen,
                    )
                else:
                    # Add fractional spellbook sources as extra 1-pip cards
                    prob = threshold_probability_multi_pip(
                        self.atlas_size, eff_K1 + int(round(sb_fractional)), eff_K2,
                        sites_seen, actual_pips
                    )

                element_probs[e] = prob
                # Compute surplus: how many 1-pip cards can you remove (or need to add)
                # and still meet target probability
                surplus = 0
                if prob >= self.target_prob:
                    # Overshoot: try removing 1-pip cards until probability drops below target
                    for remove in range(1, eff_K1 + 1):
                        test_K1 = eff_K1 - remove
                        if sb_exact > 0:
                            test_p = combined_threshold_probability(
                                self.atlas_size, test_K1, eff_K2, sites_seen, actual_pips,
                                self.spellbook_size, sb_exact, spells_seen,
                            )
                        else:
                            test_p = threshold_probability_multi_pip(
                                self.atlas_size, test_K1 + int(round(sb_fractional)), eff_K2,
                                sites_seen, actual_pips
                            )
                        if test_p < self.target_prob:
                            surplus = remove - 1
                            break
                    else:
                        surplus = eff_K1  # can remove all 1-pip and still meet target
                else:
                    # Deficit: try adding 1-pip cards until probability meets target
                    for add in range(1, self.atlas_size + 1):
                        test_K1 = eff_K1 + add
                        if sb_exact > 0:
                            test_p = combined_threshold_probability(
                                self.atlas_size, test_K1, eff_K2, sites_seen, actual_pips,
                                self.spellbook_size, sb_exact, spells_seen,
                            )
                        else:
                            test_p = threshold_probability_multi_pip(
                                self.atlas_size, test_K1 + int(round(sb_fractional)), eff_K2,
                                sites_seen, actual_pips
                            )
                        if test_p >= self.target_prob:
                            surplus = -add
                            break
                    else:
                        surplus = -5

                element_details[e] = {
                    "pips": pips,
                    "effective_pips": actual_pips,
                    "sources": base_sources,
                    "effective_sources": round(atlas_eff + sb_fractional, 2),
                    "spellbook_sources": sb_exact,
                    "K1": eff_K1,
                    "K2": eff_K2,
                    "probability": round(prob * 100, 1),
                    "surplus": surplus,
                }

            # Combined probability for multi-element
            combined_prob = 1.0
            for p in element_probs.values():
                combined_prob *= p

            # Find recommended sources if below target
            recommendations = {}
            if combined_prob < self.target_prob:
                for e, pips in needed.items():
                    actual_pips = max(0, pips - (1 if self.is_elementalist else 0))
                    needed_sources = find_min_sources(
                        self.atlas_size, sites_seen, actual_pips, self.target_prob
                    )
                    current = element_sources.get(e, 0)
                    if current < needed_sources:
                        recommendations[e] = {
                            "have": current,
                            "need": needed_sources,
                            "deficit": needed_sources - current,
                        }

                # Multi-element: check if +1 each would help
                if len(needed) > 1 and not recommendations:
                    # Frank Karsten method: add +1 source per element
                    for e, pips in needed.items():
                        actual_pips = max(0, pips - (1 if self.is_elementalist else 0))
                        current = element_sources.get(e, 0)
                        recommendations[e] = {
                            "have": current,
                            "need": current + 1,
                            "deficit": 1,
                            "note": "Multi-element spell: +1 recommended per Frank Karsten method",
                        }

            # Later turn probability (can we cast it later?)
            later_turn_prob = None
            if combined_prob < self.target_prob:
                for future_turn in range(turn + 1, turn + 4):
                    future_sites = get_sites_seen(self.archetype, future_turn, self.on_the_play, self.draw_schedule)
                    future_sites = int(round(future_sites + self.adj_extra_sites))
                    if future_sites == sites_seen:
                        continue
                    future_spells = get_spells_seen(future_turn, self.on_the_play, self.draw_schedule)
                    future_spells = int(round(future_spells + self.adj_extra_spells))
                    future_prob = 1.0
                    for e, pips in needed.items():
                        actual_pips = max(0, pips - (1 if self.is_elementalist else 0))
                        special_adj = self._get_special_site_adjustments(e, pips)
                        manual_pips = self.adj_extra_pips.get(e, 0)
                        eff_K1 = element_K1.get(e, 0) + int(round(special_adj + manual_pips))
                        eff_K2 = element_K2.get(e, 0)
                        sb_exact = self._count_spellbook_sources_exact(e)
                        if sb_exact > 0:
                            ep = combined_threshold_probability(
                                self.atlas_size, eff_K1, eff_K2, future_sites, actual_pips,
                                self.spellbook_size, sb_exact, future_spells,
                            )
                        else:
                            sb_frac = self._count_spellbook_sources(e)
                            ep = threshold_probability_multi_pip(
                                self.atlas_size, eff_K1 + int(round(sb_frac)), eff_K2,
                                future_sites, actual_pips
                            )
                        future_prob *= ep
                    if future_prob >= self.target_prob:
                        later_turn_prob = {
                            "turn": future_turn,
                            "probability": round(future_prob * 100, 1),
                        }
                        break

            spell_analysis.append({
                "name": name,
                "qty": qty,
                "cost": cost,
                "threshold": {e: pips for e, pips in needed.items()},
                "target_turn": turn,
                "sites_seen": sites_seen,
                "spells_seen": spells_seen,
                "draw_probability": round(draw_prob * 100, 1),
                "element_details": element_details,
                "combined_probability": round(combined_prob * 100, 1),
                "meets_target": combined_prob >= self.target_prob,
                "recommendations": recommendations if recommendations else None,
                "later_turn": later_turn_prob,
            })

        # Sort: flagged spells first, then by cost
        spell_analysis.sort(key=lambda x: (x["meets_target"], x["cost"]))

        # Assumptions summary
        arch_info = ARCHETYPE_ASSUMPTIONS.get(self.archetype, ARCHETYPE_ASSUMPTIONS["midrange"])
        assumptions = {
            "archetype": arch_info["description"] if not self.draw_schedule else "Custom",
            "on_the_play": self.on_the_play,
            "play_draw": "On the play (no draw T1)" if self.on_the_play else "On the draw (+1 card seen)",
            "atlas_size": self.atlas_size,
            "spellbook_size": self.spellbook_size,
            "target_probability": f"{self.target_prob * 100:.0f}%",
            "mulligan_sites": arch_info["mulligan_sites"] if not self.draw_schedule else self.draw_schedule.get("mulligan_sites", 0),
            "draw_schedule": self.draw_schedule,
            "site_draw_pattern": arch_info["site_draw_pattern"],
            "notes": arch_info["notes"],
            "elementalist_bonus": self.is_elementalist,
            "seer_bonus": self.is_seer,
            "extra_sites": self.adj_extra_sites,
            "extra_spells": self.adj_extra_spells,
            "extra_pips": {e: v for e, v in self.adj_extra_pips.items() if v > 0},
            "sites_seen_bonus": self._get_sites_seen_breakdown(),
        }

        return {
            "assumptions": assumptions,
            "element_sources": {
                e: {"count": element_sources.get(e, 0), "contributors": element_contributors.get(e, [])}
                for e in ELEMENTS if element_sources.get(e, 0) > 0
            },
            "spells": spell_analysis,
            "flagged_count": sum(1 for s in spell_analysis if not s["meets_target"]),
        }

    # --- Full Report ---

    def _find_special_cards_in_deck(self) -> list:
        """Find special cards present in this deck and return their notes."""
        found = []
        all_card_names = set()
        if self.avatar:
            all_card_names.add(self.avatar["name"])
        for _, name, qty in self.spellbook:
            all_card_names.add(name)
        for _, name, qty in self.atlas:
            all_card_names.add(name)

        # Conditions that are fully modeled in calculations
        MODELED_CONDITIONS = {
            "pay_mana", "innately_flooded", "conditional_threshold",
            "in_play", "always", "archetype_override", "conditional_note",
            "draw_site", "draw_site_deathrite", "draw_and_play_site",
            "play_top_site", "play_top_site_deathrite",
        }

        for section in ["sites", "avatars", "spellbook_sources"]:
            for card_name, info in SPECIAL_CARDS.get(section, {}).items():
                if card_name in all_card_names:
                    condition = info.get("condition", "")
                    # Check if modeled via sites_seen_trigger
                    has_trigger = "sites_seen_trigger" in info
                    found.append({
                        "name": card_name,
                        "section": section,
                        "note": info.get("note", ""),
                        "condition": condition,
                        "modeled": condition in MODELED_CONDITIONS or has_trigger,
                    })
        return found

    def full_report(self) -> dict:
        """Generate the complete deck analysis report."""
        return {
            "stats": self.all_stats(),
            "threshold": self.threshold_analysis(),
            "unmatched_cards": self.unmatched,
            "special_cards_in_deck": self._find_special_cards_in_deck(),
        }


# ---------------------------------------------------------------------------
# Threshold Table Generator (like the article's tables)
# ---------------------------------------------------------------------------

def generate_threshold_table(
    archetype: str = "midrange",
    atlas_size: int = 30,
    on_the_play: bool = True,
    max_sources: int = 25,
) -> dict:
    """Generate a full threshold probability table for an archetype.

    Returns a dict with turns as keys and lists of (sources, pips, probability) tuples.
    """
    table = SITES_SEEN.get(archetype, SITES_SEEN["midrange"])
    turns = sorted(set(table.values()))

    results = {}
    for sites_seen in turns:
        # Find which turns map to this sites_seen value
        turn_labels = [t for t, s in table.items() if s == sites_seen]
        if not on_the_play:
            sites_seen += 1

        turn_label = "/".join(str(t) for t in sorted(turn_labels))
        results[turn_label] = {}

        for pips in range(1, 5):
            probs = []
            for sources in range(1, max_sources + 1):
                prob = threshold_probability(atlas_size, sources, sites_seen, pips)
                probs.append((sources, round(prob * 100, 1)))
            results[turn_label][pips] = probs

    return results


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_threshold_str(threshold: dict) -> str:
    """Format threshold dict like {fire: 2, water: 1} to 'FF W'."""
    parts = []
    for e in ELEMENTS:
        pips = threshold.get(e, 0)
        if pips > 0:
            parts.append(ELEMENT_LETTERS[e] * pips)
    return " ".join(parts)


def format_report_text(report: dict) -> str:
    """Format the full report as plain text."""
    lines = []

    # --- Overview ---
    stats = report["stats"]
    ov = stats["overview"]
    lines.append("=" * 60)
    lines.append("SORCERY DECK ANALYSIS")
    lines.append("=" * 60)
    lines.append(f"Avatar: {ov['avatar']}")
    if ov["avatar_ability"]:
        lines.append(f"  Ability: {ov['avatar_ability']}")
    lines.append(f"Spellbook: {ov['spellbook_count']} cards | Atlas: {ov['atlas_count']} sites | Total: {ov['total_count']}")
    lines.append("")

    # --- Mana Curve ---
    mc = stats["mana_curve"]
    lines.append("MANA CURVE")
    lines.append("-" * 40)
    if mc["distribution"]:
        max_count = max(mc["distribution"].values()) if mc["distribution"] else 1
        for cost in range(0, max(mc["distribution"].keys()) + 1):
            count = mc["distribution"].get(cost, 0)
            bar = "#" * int(count / max_count * 20) if max_count > 0 else ""
            lines.append(f"  {cost:>2} mana: {bar} {count}")
        lines.append(f"  Average: {mc['average']} | Median: {mc['median']}")
    lines.append("")

    # --- Type Breakdown ---
    tb = stats["type_breakdown"]
    lines.append("CARD TYPE BREAKDOWN")
    lines.append("-" * 40)
    for t, c in tb["types"].items():
        lines.append(f"  {t}: {c}")
    if tb["minion_subtypes"]:
        lines.append("  Minion subtypes:")
        for st, c in tb["minion_subtypes"].items():
            lines.append(f"    {st}: {c}")
    lines.append("")

    # --- Element Distribution ---
    ed = stats["element_distribution"]
    lines.append("ELEMENT DISTRIBUTION")
    lines.append("-" * 40)
    lines.append("  Spellbook:")
    for e, c in ed["spellbook_elements"].items():
        lines.append(f"    {e.capitalize()}: {c} cards")
    lines.append("  Atlas sources:")
    for e, c in ed["atlas_sources"].items():
        lines.append(f"    {e.capitalize()}: {c} sites")
    if ed["multi_element_sites"]:
        lines.append("  Multi-element sites:")
        for name, elems, qty in ed["multi_element_sites"]:
            e_str = "/".join(e.capitalize() for e in elems)
            lines.append(f"    {qty}x {name} ({e_str})")
    if ed.get("special_sites"):
        lines.append("  Special threshold sites:")
        for name, elems, qty, condition in ed["special_sites"]:
            e_str = "/".join(e.capitalize() for e in elems) if isinstance(elems, list) else str(elems)
            cond_label = {"pay_mana": "costs 1 mana", "genesis_choose": "choose on genesis",
                          "copy": "copies nearby site"}.get(condition, condition)
            lines.append(f"    {qty}x {name} ({e_str}) -- {cond_label}")
    lines.append("")

    # --- Keywords ---
    kw = stats["keyword_census"]
    if kw:
        lines.append("KEYWORD CENSUS")
        lines.append("-" * 40)
        for k, c in kw.items():
            lines.append(f"  {k}: {c}")
        lines.append("")

    # --- Combat Stats ---
    cs = stats["combat_stats"]
    if cs:
        lines.append("COMBAT STATS (Minions)")
        lines.append("-" * 40)
        if "avg_attack" in cs:
            lines.append(f"  Attack  — Avg: {cs['avg_attack']}, Median: {cs['median_attack']}")
        if "avg_defence" in cs:
            lines.append(f"  Defence — Avg: {cs['avg_defence']}, Median: {cs['median_defence']}")
        lines.append("")

    # --- Rarity ---
    rb = stats["rarity_breakdown"]
    if rb:
        lines.append("RARITY BREAKDOWN")
        lines.append("-" * 40)
        for r, c in rb.items():
            lines.append(f"  {r}: {c}")
        lines.append("")

    # --- Threshold Analysis ---
    th = report["threshold"]
    assumptions = th["assumptions"]
    lines.append("=" * 60)
    lines.append(f"THRESHOLD ANALYSIS ({assumptions['archetype']}, "
                 f"{'on the play' if assumptions['on_the_play'] else 'on the draw'}, "
                 f"{assumptions['target_probability']} target)")
    lines.append("=" * 60)
    lines.append("Assumptions:")
    lines.append(f"  - {assumptions['play_draw']}")
    lines.append(f"  - Atlas: {assumptions['atlas_size']} sites | Spellbook: {assumptions['spellbook_size']} spells")
    lines.append(f"  - Mulligan: see {assumptions['mulligan_sites']} atlas cards")
    lines.append(f"  - Site draw: {assumptions['site_draw_pattern']}")
    lines.append(f"  - {assumptions['notes']}")
    if assumptions["elementalist_bonus"]:
        lines.append("  - Elementalist: +1 of each element (threshold pips reduced by 1)")
    if assumptions["seer_bonus"]:
        lines.append("  - Seer: scry ability provides extra atlas looks")
    lines.append("")

    # Element sources
    lines.append("Element Sources in Atlas:")
    for e, info in th["element_sources"].items():
        contribs = ", ".join(info["contributors"])
        lines.append(f"  {e.capitalize():>6}: {info['count']} sources ({contribs})")
    lines.append("")

    # Per-spell table
    spells = th["spells"]
    if spells:
        lines.append(f"{'Spell':<28} {'Qty':>3} {'Cost':>4} {'Threshold':<10} {'Turn':>4} {'Sites':>5} {'Prob':>7} {'':>3}")
        lines.append("-" * 72)
        for sp in spells:
            th_str = format_threshold_str(sp["threshold"])
            flag = " !!" if not sp["meets_target"] else ""
            lines.append(
                f"{sp['name']:<28} {sp['qty']:>3} {sp['cost']:>4} {th_str:<10} "
                f"T{sp['target_turn']:>3} {sp['sites_seen']:>5} "
                f"{sp['combined_probability']:>6.1f}%{flag}"
            )
        lines.append("")

    # Flagged spells
    flagged = [s for s in spells if not s["meets_target"]]
    if flagged:
        lines.append(f"!! {len(flagged)} spell(s) below {assumptions['target_probability']} target:")
        for sp in flagged:
            line = f"  {sp['name']} ({sp['combined_probability']}%)"
            if sp.get("recommendations"):
                recs = []
                for e, r in sp["recommendations"].items():
                    recs.append(f"need {r['need']} {e} (have {r['have']})")
                line += " -- " + ", ".join(recs)
            if sp.get("later_turn"):
                lt = sp["later_turn"]
                line += f" | Castable T{lt['turn']} at {lt['probability']}%"
            lines.append(line)
        lines.append("")

    # Unmatched cards
    if report.get("unmatched_cards"):
        lines.append("UNMATCHED CARDS (not found in database):")
        for section, name, qty in report["unmatched_cards"]:
            lines.append(f"  [{section}] {qty}x {name}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sorcery: Contested Realm Deck Analyzer")
    parser.add_argument("--json", type=str, help="Deck JSON string")
    parser.add_argument("--json-file", type=str, help="Path to deck JSON file")
    parser.add_argument("--archetype", type=str, default="midrange",
                        choices=["aggro", "midrange", "pathfinder"],
                        help="Deck archetype for threshold calculations")
    parser.add_argument("--on-the-draw", action="store_true", default=False,
                        help="Calculate as if on the draw (extra card seen)")
    parser.add_argument("--target", type=float, default=0.90,
                        help="Target probability threshold (default: 0.90)")
    parser.add_argument("--table", action="store_true",
                        help="Print threshold probability table for archetype")
    parser.add_argument("--output", type=str, choices=["text", "json"], default="text",
                        help="Output format")

    args = parser.parse_args()

    db = CardDB()

    # Table mode
    if args.table:
        table = generate_threshold_table(
            archetype=args.archetype,
            on_the_play=not args.on_the_draw,
        )
        if args.output == "json":
            print(json.dumps(table, indent=2))
        else:
            arch_info = ARCHETYPE_ASSUMPTIONS.get(args.archetype, ARCHETYPE_ASSUMPTIONS["midrange"])
            print(f"\n{arch_info['description']} Threshold Table (30-card Atlas, "
                  f"{'on the play' if not args.on_the_draw else 'on the draw'})")
            print("=" * 60)
            for turn_label, pips_data in table.items():
                print(f"\nTurn {turn_label}:")
                for pips, probs in pips_data.items():
                    # Show sources that get >=90%
                    min_src = next((s for s, p in probs if p >= 90), None)
                    print(f"  {pips} pip(s): min {min_src} sources for 90%+ "
                          f"(at {min_src}: {next(p for s, p in probs if s == min_src)}%)" if min_src else
                          f"  {pips} pip(s): cannot reach 90% with available sources")
        return

    # Deck analysis mode
    deck = None
    if args.json:
        deck = json.loads(args.json)
    elif args.json_file:
        with open(args.json_file, encoding="utf-8") as f:
            deck = json.load(f)

    if not deck:
        print("Error: provide --json or --json-file with deck data")
        print("Format: {\"avatar\": [[name, qty]], \"spellbook\": [[name, qty], ...], \"atlas\": [[name, qty], ...]}")
        sys.exit(1)

    analyzer = DeckAnalyzer(
        deck=deck,
        db=db,
        archetype=args.archetype,
        on_the_play=not args.on_the_draw,
        target_prob=args.target,
    )

    report = analyzer.full_report()

    if args.output == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report_text(report))


if __name__ == "__main__":
    main()
