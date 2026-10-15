"""keys: the bonding-curve PDA against five vault owners seen in the check 4.1 diagnosis of 2026-10-15 (the in_trade
transfer's far side of real curve trades), and the on-curve test on wallets, program ids and PDAs."""

from wallets import keys

CURVES = [("21P1uq7zQpP9omUDipZ1Wsb21oqD5YwuakDc6t1epump", "Es1qkAsv3PzxtCg5giSdV4obCEPxnYzaiKghV8kUdJdv"),
          ("jPSnEE9HtrzUExJU91bFc4q9HvfaYV4gtvP3GLGpump", "6bmc4aTf6ZgZGyWfBFdUpH7LoHaUB5d3dWsnG1LnkcF1"),
          ("8mbb4RRnwXzzM6x6MRvDL5wqNBwshWjzbLiqqitgpump", "GR1X5vFcpPT39n2hCPrtiqPUXvySFvUZQVgARy7B3vBW"),
          ("FbpGgXiijgJRsdfbaFrwpHdkGFT3JaS9BgbbjzZ3pump", "3nDzjXzTPnwEzji2nbdUgtSSm57s9bocxmoBCbGwW7SD"),
          ("AMy4uxQPH7sEyu7EENWxADwxhtSet5qXVLQKny5apump", "HUMT9mL8X925wRnN7UyxUz5KaxaDym1toKoSYxK7xtk4")]


def test_curve_pda_matches_the_chain():
    for mint, curve in CURVES:
        assert keys.curve_pda(mint) == curve
    assert keys.curve_pda("Mpump") == keys.curve_pda("Mpump") and keys.curve_pda("Mpump") != keys.curve_pda("M2pump")  # fixtures: utf-8 seed


def test_on_curve_wallets_and_pdas():
    for k in ("11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
              "HTVZVEQMBsNanubDPTs3CxDAEGNFQHJY8c1441iy2S5r", "3Jm6wArnoLWVE5VmHtB3b9kVRBU9YKictf4GX4o7K9Vh"):
        assert not keys.is_pda(k), k
    # the sniper ARu4n5mF… and the sellers it feeds are program-derived (the extractor flags their rows is_vault)
    for k in ("ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gdLT5SZn", "FtfQi1xHPBGGHoBYo94XDpgXJegops7imggLuURDT8uh", "Es1qkAsv3PzxtCg5giSdV4obCEPxnYzaiKghV8kUdJdv",
              "FubaWMVESWfGsdnoo3JKDx3CNsL7NzYqJAPpDGGB5JLF"):
        assert keys.is_pda(k), k
    assert keys.b58encode(keys.b58decode(CURVES[0][0])) == CURVES[0][0] and not keys.is_pda("Mpump")
