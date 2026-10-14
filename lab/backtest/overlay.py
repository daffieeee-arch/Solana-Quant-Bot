"""O7: insider inventory as an entry veto and an exit (week2-specs-S1-O7-2026-10-10.md).

Insiders of a token: its creator and every wallet that bought in [c, c + 10 s] (c = create slot);
the set is fixed at c + 10 s. (The spec's third group, wallets whose early buy was paid by the
create transaction's fee payer, is a subset of the second.) Holdings come from net trade flows
on the curve and the token's migration pool; transfers are invisible, so an insider that moves
tokens to another wallet and sells there is not seen.

  ins_share(t) = H_ins(t) / circ(t), with H_ins the insiders' and circ everybody's net bought tokens.
  It is undefined (None, with a reason) when the store cannot measure it (vins-definition-2026-10-15.md,
  sha256 b7181aef): circ <= 0 (circ_le_0), H_ins > circ (h_ins_gt_circ: tokens moved to sell wallets
  out of the store's sight), H_ins < 0 (h_ins_lt_0), or the insider set unknown (insiders_unknown).
  No cap at 1 and no corrected share. Vetoes are fail-closed: undefined vetoes (vetoes()).
  V1 veto: no entry when ins_share > 25% at the decision, or undefined.
  V2 exit: sell when the insiders have net sold >= 25% of H_ins(entry) since the entry, or the
           creator sold anything; the family's own exit rule still applies (first one wins).
  V3: both.
Overlays are evaluated on the same triggers as the base family, so the difference is paired.
"""

import bisect

from .strategies import ExitRule

INSIDER_WINDOW_MS = 10_000
VETO_SHARE = 0.25
EXIT_SOLD = 0.25
VARIANTS = ("O7V1", "O7V2", "O7V3")
UNDEFINED = ("circ_le_0", "h_ins_gt_circ", "h_ins_lt_0", "insiders_unknown")


def vetoes(share, threshold):
    """A fail-closed veto: an undefined share (None) vetoes like one above the threshold."""
    return share is None or share > threshold


class Insiders:
    """Insider holdings of one token stream as prefix sums over its curve and pool legs.

    The stream must hold the token's history up to the slots asked: curve legs from the create on
    (streams.load_streams does), pool legs from the pool's start, or `before` = (insider net tokens,
    everybody's net tokens, creator sells) of the pool trades before the stream's pool window
    (tournament.token_insiders). A later window start alone hides earlier insider sells."""

    def __init__(self, stream, window_slots, before=None):
        c = stream.create_slot
        legs = []
        for l in stream.curve:
            legs.append((l.slot, l.trader, l.t if l.is_buy else -l.t))
        if stream.orientation == "normal":
            for l in stream.pool_legs:
                if l.kind in ("buy", "sell"):
                    legs.append((l.slot, l.trader, l.base if l.kind == "buy" else -l.base))
        legs.sort(key=lambda x: x[0])
        self.known = c is not None and bool(stream.curve) and stream.curve[0].slot <= c + window_slots
        self.insiders = {stream.creator} | {w for s, w, d in legs if c is not None and s <= c + window_slots and d > 0}
        h0, c0, s0 = before or (0, 0, 0)
        self.slots, self.h_ins, self.circ, self.creator_sold = [], [h0], [c0], [s0]
        for s, w, d in legs:
            self.slots.append(s)
            self.h_ins.append(self.h_ins[-1] + (d if w in self.insiders else 0))
            self.circ.append(self.circ[-1] + d)
            self.creator_sold.append(self.creator_sold[-1] + (1 if w == stream.creator and d < 0 else 0))

    def _i(self, slot):
        return bisect.bisect_right(self.slots, slot)

    def share_reason(self, t):
        """(ins_share, None), or (None, reason) when the share is undefined (see the module doc)."""
        if not self.known:
            return None, "insiders_unknown"
        i = self._i(t)
        h, c = self.h_ins[i], self.circ[i]
        if c <= 0:
            return None, "circ_le_0"
        if h < 0:
            return None, "h_ins_lt_0"
        if h > c:
            return None, "h_ins_gt_circ"
        return h / c, None

    def share(self, t):
        """ins_share(t) in [0, 1], or None when undefined (a comparison with None fails loudly)."""
        return self.share_reason(t)[0]

    def holdings(self, t):
        return self.h_ins[self._i(t)]

    def sold_since(self, a, b):
        """(insider net tokens sold, creator sells) over a < slot <= b."""
        i, j = self._i(a), self._i(b)
        return self.h_ins[i] - self.h_ins[j], self.creator_sold[j] - self.creator_sold[i]


class O7Exit(ExitRule):
    """Wraps a family exit rule with the O7 insider exit."""

    def __init__(self, base, insiders):
        self.base, self.ins, self.h0 = base, insiders, None

    def deadline(self, entry_slot):
        return self.base.deadline(entry_slot)

    def __call__(self, ctx):
        if self.h0 is None:
            self.h0 = self.ins.holdings(ctx["entry_slot"])
        sold, creator_sells = self.ins.sold_since(ctx["entry_slot"], ctx["slot"])
        if creator_sells > 0:
            return "o7_creator_sell"
        if self.h0 > 0 and sold >= EXIT_SOLD * self.h0:
            return "o7_insider_sell"
        return self.base(ctx)
