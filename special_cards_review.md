# Special Cards That Modify Calculations

Reference for reviewing cards in `special_cards.json`.
Cards are split into those that **actually change the threshold %** vs those that are **documented only**.

---

# PART 1 — IMPLEMENTED (affects threshold %)

These cards have code in `sorcery_threshold.py` that modifies the hypergeometric probability output.

## Avatars — Implemented

| Card | How It Modifies Calc | Code Location |
|------|---------------------|---------------|
| Elementalist | `actual_pips = max(0, pips - 1)` — reduces every element's required pips by 1 | `_count_atlas_sources` + `threshold_analysis` |
| Pathfinder | Forces `pathfinder` archetype — 0 opening sites, plays top site each turn | `__init__`, archetype override |

**Comments:**
-
-

---

## Sites — Counted as Full Sources (added to atlas pip count)

These are handled in `_count_atlas_sources()`. When JSON threshold is 0 but the card is in `special_cards.json`, it gets counted as a real source.

| Card | Condition | Element(s) | Pips Added | Notes |
|------|-----------|------------|------------|-------|
| Annual Fair | `pay_mana` | A/E/F/W | 1 each | Full source, labeled "(costs 1 mana)" |
| Mismanaged Mortuary | `innately_flooded` | W | 1 | Full water source, labeled "(flooded)" |
| City of Glass | `conditional_threshold` | A | 1 | Full source with downside |
| City of Plenty | `conditional_threshold` | W | 1 | Full source with downside |
| City of Souls | `conditional_threshold` | E | 1 | Full source with downside |
| City of Traitors | `conditional_threshold` | F | 1 | Full source with downside |
| The Empyrean | `conditional_threshold` | A/E/F/W | 1 each | Full source (requires Angel/Ward nearby) |

**Comments:**
- The 4 City dont give threshold. Remove from the list. 
-

---

## Sites — Fractional Adjustments (added via `_get_special_site_adjustments()`)

These don't add full pips — they add a fractional float to `atlas_eff` before the probability calc.

| Card | Condition | Element(s) | Adjustment | Logic |
|------|-----------|------------|------------|-------|
| Mirror Realm | `copy` | varies | 1 multi-pip elem = +1.0, 2 = +0.5, 3-4 = +0.25 | Only for 2+ pip spells, only for elements with multi-pip spells |
| Valley of Delight | `genesis_choose` | A/E/F/W | <=2 colors & no multi-pip = +1.0, else +0.5 | Based on deck's element spread |
| Floodplain | `flood` | W only | +0.33 | Only for 2+ water pip spells (already has 1 water innate) |

**Comments:**
- Remove mirror realm from calculations
- Valley of delight : always give 0.5 threshold of each

---

## Spellbook Sources — Implemented (fractional credit via `_count_spellbook_sources()`)

Only cards with `condition: "in_play"` are counted. The fractional scale:
`1 copy = 0.15 | 2 = 0.20 | 3 = 0.33 | 4-5 = 0.50 | 6+ = 0.75`

When exact count > 0, uses `combined_threshold_probability()` (atlas + spellbook joint probability).

| Card | Type | Element(s) | Notes |
|------|------|----------|-------|
| Ruby Core | Artifact | F | Also hardcoded in `CORE_CARDS` fallback |
| Onyx Core | Artifact | E | Also hardcoded in `CORE_CARDS` fallback |
| Aquamarine Core | Artifact | W | Also hardcoded in `CORE_CARDS` fallback |
| Amethyst Core | Artifact | A | Also hardcoded in `CORE_CARDS` fallback |
| Shrine of the Dragonlord | Artifact | A/E/F/W | All 4 elements |
| Blacksmith Family | Minion | F | |
| Castle Servants | Minion | A | |
| Common Cottagers | Minion | E | |
| Fisherman's Family | Minion | W | |

**Comments:**
-
-

---

# PART 2 — NOT IMPLEMENTED (documented only)

These cards are in `special_cards.json` for reference but have **no effect** on the threshold % calculation. They appear in `_find_special_cards_in_deck()` for display purposes only.

## Avatars — Display Only

| Card | Documented Effect | Why Not Implemented |
|------|-------------------|---------------------|
| Seer | +0.5 atlas looks/turn | `seer_bonus` flag exists but only printed in assumptions text, never modifies sites_seen |
| Avatar of Water | Flood adjacent site each turn | Would need board-state simulation |
| Waveshaper | Flood adjacent site each turn | Would need board-state simulation |
| Geomancer | Tap: play top site to replace Rubble | Would need board-state simulation |

**Comments:**
-
-

---

## Sites — Display Only

### Genesis temporary / conditional (passed over in `_count_atlas_sources`)

| Card | Condition | Documented Effect | Why Not Implemented |
|------|-----------|-------------------|---------------------|
| Algae Bloom | `genesis_temporary` | 1W innate + genesis A/E/F one turn | Temporary, hard to model |
| Autumn Bloom | `genesis_temporary` | 1E innate + genesis A/F/W one turn | Temporary, hard to model |
| Desert Bloom | `genesis_temporary` | 1F innate + genesis A/E/W one turn | Temporary, hard to model |
| Twilight Bloom | `genesis_temporary` | 1A innate + genesis E/F/W one turn | Temporary, hard to model |
| Crossroads | `scry_atlas` | Genesis: look 4, keep 1 | Would need atlas-quality simulation |
| Pristine Paradise | `conditional_note` | All 4 elements if empty | Already in JSON thresholds — no extra handling |
| The Colour Out of Space | `conditional_note` | All 4 elements if near void | Already in JSON thresholds — no extra handling |

### Threshold bypass sites

| Card | Condition | Documented Effect |
|------|-----------|-------------------|
| Pond | `ignore_threshold` | Genesis: next Beast ignores threshold |
| Tournament Grounds | `ignore_threshold` | Knights/Sirs/Dames ignore threshold |
| Dragonlord's Lair | `ignore_threshold` | Dragons ignore threshold, -1 cost |
| Den of Evil | `ignore_threshold` | Genesis: next Evil ignores threshold |
| Imperial Road | `play_extra_site` | Genesis: play extra site |

**Comments:**
-
-

---

## Spellbook Sources — Display Only

### Cards with non-`in_play` conditions (skipped by `_count_spellbook_sources_exact`)

| Card | Condition | Documented Effect |
|------|-----------|-------------------|
| Tide Naiads | `flood_site` | Floods its site to water |
| Great Old One | `flood_all` | Genesis: floods entire realm |
| Land Surveyor | `draw_site` | Genesis: draw a site |
| Kettletop Leprechaun | `draw_site_deathrite` | Deathrite: draw a site |
| Frontier Settlers | `play_top_site` | Tap: play top site |
| Landmass | `draw_and_play_site` | Magic: draw + play land site |
| Overflow | `draw_and_play_site` | Magic: draw + play water site |
| Sow the Earth | `play_top_site` | Aura: play top site (double mana/threshold) |
| Clay Golem | `play_top_site_deathrite` | Deathrite: play top site |

**Comments:**
- I want Kettletop Leprechaun and Land Surveyor to count toward the number of sites seen.
- Same for Landmass and Overflow


### Threshold bypass spells

| Card | Condition | Documented Effect |
|------|-----------|-------------------|
| Mix Aer | `ignore_threshold` | Sacrifice: next Air spell no threshold, -2 cost |
| Mix Aqua | `ignore_threshold` | Sacrifice: next Water spell no threshold, -2 cost |
| Mix Ignis | `ignore_threshold` | Sacrifice: next Fire spell no threshold, -2 cost |
| Mix Terra | `ignore_threshold` | Sacrifice: next Earth spell no threshold, -2 cost |
| Four Waters of Paradise | `ignore_threshold` | Sacrifice: next elemental no threshold, -4 cost |
| Captain Baldassare | `ignore_threshold` | Cast opponent's discards ignoring threshold |
| Sea Raider | `ignore_threshold` | Cast killed enemy's spell ignoring threshold |
| De Vermis Mysteriis | `ignore_threshold` | Bearer casts minions for (5) ignoring threshold |
| Wiccan Tools | `ignore_threshold` | Sacrifice: bearer's spells no threshold this turn |

### Threshold removal (anti-synergy)

| Card | Condition | Documented Effect |
|------|-----------|-------------------|
| Drought | `remove_threshold` | Aura: no water threshold on affected sites |
| Sinterfee | `remove_threshold` | Genesis: silences adjacent site |
| Atlantean Fate | `flood_aura` | Aura: non-Ordinary sites flooded (water only) |
| Flood | `flood_aura` | Aura: sites become flooded (water only) |

**Comments:**
-
-

---

# Change Log

| Date | Card(s) | Change | Reason |
|------|---------|--------|--------|
| | | | |
| | | | |
| | | | |
