"""H5 (research/hypothesis-h5-flow300-tp100-organic-2026-10-16.md, sha256 c59c6dfd, LAB research, #167): K3's entry
(F7 pool_reversed, 0.5 SOL, q 0.5, d_ms 600) with the exit flow300_tp100_H6 on the organic triggers, an absolute test on the clean data (1055-1063), a run of its own
after E20, H3 and H4 on E20's store. The job is backtest.clean_hx with this Spec (nas_clean_hx.sh, SPEC=h5).

  python -m backtest.clean_h5 --run DIR --store FILE --out DIR --hypothesis FILE --exclusions FILE \\
      --exclusions-sha256 SHA --engine SHA --engine-dir DIR --trials-parts DIR [--dry-run --e20-run DIR]
"""

from .clean_hx import Spec, main

SPEC = Spec(
    name="H5",
    hypothesis="hypothesis-h5-flow300-tp100-organic-2026-10-16.md",
    sha="c59c6dfd42df9890efd0384622e1638c8c5c373973c082d5cbf91141fc90583e",
    variant="flow300_tp100_H6",
    # The pin's replay files: main after the big take-profits (4dbd34b, #293), the engine of job 87.
    replay_sha256={
        "tournament.py": "c348ba68e9d70a444ec4f5317ea364b7a1a10c0b9e7d091266bbe580a6bb675b",
        "strategies.py": "935dd6f2182e4f5d8376a8c7dd123339f0fb055e5dec80ffe401779702cfb5f1",
        "replay.py": "fd2e3c91f7f10a63682b4a8ecde7c04ddcc5a950626cebc4514b1d42d0d5946e",
        "costs.py": "27db4d6d9d95a0fb5bf797573aa968d01c1858820d5e16b2625eccf056fa7077",
        "wash.py": "8486a0d289ffc826cb96961b243cfb777e1b774bd6231bbd7b221b3a5dcc8440",
        "venues.py": "bce5c80b7f729459c738b989de5e7ef6592f63fbeeb0112b28fb4f349ab13f03",
        "streams.py": "201412f735647789a37a617adfe8e8b18a6ada088832c3bb371bfe0ba152467e",
        "mechanics.py": "3779c94287de2e030482b679cb52ccb907d8ad10cff038b6a00539f9ff392ad0",
        "overlay.py": "1b606ee7320d4cf4efd0aa9a386485f211f3a1cc97eaf362429a2b6ad86a1d65",
        "data.py": "5ea7765cbf8cca4d1c1cd97dac01f87705f2dc3c16e06fd18328af6b072a8709",
    },
    trials=258,  # N: H4's 210 and job 87's 48 (the H5 file)
    trials_run="bigtp-dev-1033-1046-4dbd34b",  # job 87: F7 pool_reversed v1 + big x d 1, 2 x farm flagged, organic
    trials_columns=48,
    f7_grid="v1,exits,long,big",
    describe=("tp4_T600", "hold180", "flow300_H6", "tp50_H6", "tp100_H6"),
    common_with="flow300_H6",
    claim="F7 organic met een grote take-profit verdient op schone data netto SOL per dag (reconstructie met het kostenmodel)",
)

if __name__ == "__main__":
    main(SPEC)
