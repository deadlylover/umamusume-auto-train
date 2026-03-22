# Trackblazer Item Use Strategy

Working design doc for automated item use in the Trackblazer scenario.

For the canonical current execute-mode behavior around pre-action item planning and `Reset Whistle` reassess, see [`docs/TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md`](./TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md).

## 1. Cleat Hammer Strategy (First Implementation)

### Overview

Cleat hammers boost race performance. The Twinkle Star Climax (TSC) is a 3-race finale where placements award Victory Points — hammers are highest-value there. The strategy is: **earmark 3 hammers for TSC, spend surplus on G1 races.**

### Hammer Tiers (best → worst)

| Internal Name | Display Name | Effectiveness | Notes |
|---|---|---|---|
| `master_cleat_hammer` | Master Cleat Hammer | 35% | Best tier, prioritize saving for TSC |
| `artisan_cleat_hammer` | Artisan Cleat Hammer | lower | Weaker tier |
| *(future tiers TBD)* | | | Asset collection incomplete |

### Reservation Logic

**Goal:** Always reserve the 3 best hammers for TSC. Surplus hammers (beyond 3) are free to spend on G1 races during the career.

**Waterfall rule:**
1. Sort all held hammers by tier: `master > artisan > (future tiers)`
2. The top 3 hammers (by tier) are **reserved for TSC** — never spend these.
3. Any hammers beyond the top 3 are **spendable** on G1 races.
4. When spending, use the **worst available spendable hammer** first (don't waste a good one on a random G1).

**Example scenarios:**

| Inventory | Reserved for TSC | Spendable |
|---|---|---|
| 2 master, 2 artisan | 2 master + 1 artisan | 1 artisan (use on G1) |
| 3 master, 1 artisan | 3 master | 1 artisan (use on G1) |
| 1 master, 1 artisan | 1 master + 1 artisan | none (only 2, save both) |
| 0 master, 5 artisan | 3 artisan | 2 artisan (use on G1s) |
| 3 master, 0 artisan | 3 master | none |

### When to Spend Surplus Hammers

- **Trigger:** Bot is about to race a **G1** race (pre-race phase).
- **Flow:** Open Training Items menu → increment the chosen hammer → confirm use → close → proceed to race.
- **Do NOT spend on:** G2, G3, OP, Pre-OP races. Only G1.

### When to Use Reserved Hammers

- **Trigger:** Bot enters **Twinkle Star Climax** phase (3 consecutive races at end of Senior year).
- **Flow:** Before each TSC race, use 1 reserved hammer (best available).
- Use `master_cleat_hammer` first during TSC (maximize impact on the highest-stakes races).

### Implementation Notes

- The inventory scan already detects both hammer types and their quantities (`held_quantity`).
- `prepare_training_items_for_use()` already handles open → scan → increment → (skip confirm) → close. The real flow needs to press confirm.
- The hammer decision needs to run in the **pre-race** path, not the training path. Currently the bot flows through `do_race` in `actions.py` — the item-use step would slot in before the race click.
- Need to know: is this a G1 race? This info should be available from state (race grade detection or race schedule lookup).
- Need to know: is this TSC? The bot should detect the TSC phase (likely from turn/year or a TSC-specific UI element).

### Config

Draft config shape (in `config.json` under a `trackblazer` or `item_strategy` key):

```json
{
  "trackblazer": {
    "item_use_policy": {
      "version": 1,
      "settings": {
        "training_behavior": {
          "burst_commit_mode": "blast_now",
          "promote_charm_training_to_burst": true,
          "enforce_future_summer_good_luck_charm_reserve": false,
          "future_summer_good_luck_charm_min_reserve": 0
        }
      },
      "items": {
        "good_luck_charm": {
          "priority": "MED",
          "reserve_quantity": 0
        }
      }
    }
  }
}
```

Current live behavior uses `promote_charm_training_to_burst=true` with `burst_commit_mode="blast_now"`.

The future-summer reserve fields are scaffolding for later policy work. They are intentionally surfaced in config/operator console now so conservation rules can be added without another schema change.

---

## 2. Future Item Categories (Not Yet Designed)

Placeholder sections for later strategy work.

### Energy Items
- vita_65, vita_20, energy_drink_max, berry_sweet_cupcake, fluffy_pillow, grilled_carrots, royal_kale_juice, yumy_cat_food
- Trigger: energy below threshold + high-value training turn
- Classic Summer burst: stockpile energy items, layer with training boosts

### Training Boost Items
- megaphones (motivating, coaching, empowering): +training% for N turns
- ankle_weights (speed, stamina, power, guts): stat-specific boost
- scrolls, notepads, manuals, training_applications: stat-specific
- practice_drills_dvd, reporters_binoculars: general boost
- Trigger: burst training windows (Classic Summer), high-support-count turns

### Mood Items
- aroma_diffuser, motivating_megaphone
- Trigger: mood below "good"

### Condition Items
- miracle_cure, reset_whistle
- Trigger: bad condition detected, high failure rate

### Stockpile Strategy
- Junior → Classic Spring: buy from shop, don't use boosts
- Classic Summer: burst — layer megaphones + ankle_weights + energy
- Classic Fall → Senior: reactive use based on state
- Late Senior → TSC: use remaining items aggressively, keep hammer reserves
