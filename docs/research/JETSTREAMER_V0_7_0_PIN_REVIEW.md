# Jetstreamer v0.7.0 pin review

> **Document status: ACTIVE.** Formal review of candidate
> `JETSTREAMER_V0_7_0_GIT_SHA`. This is not a live acquisition pin, not an
> `ACQUISITION_LEASED` plan, and not a callback-type snapshot by another
> name. KNOWN_ISSUES #6 stays open.
>
> Official documentation hosts cited here are `DOCUMENTATION_ONLY`.
> Access date: **2026-09-05**.

## Candidate

| Field | Value |
| --- | --- |
| Git SHA | `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24` |
| Upstream tag (repo metadata) | `jetstreamer-firehose@v0.7.0` |
| Upstream repository (repo metadata) | `https://github.com/anza-xyz/jetstreamer.git` |
| Vendored crate | `rust/jetstreamer-v0-7-callback-types` (`jetstreamer-firehose` 0.7.0) |
| Firehose.rs content SHA-256 | `572ec56122e898f2318adc34d5998296b4fa0d31cf4bcab40950f5615a526a00` |
| Reducer constant | `JETSTREAMER_V0_7_0_GIT_SHA` in `rust/old-faithful-pump-reducer/src/lib.rs` |

Repo evidence shows this SHA is the identity of a **transport-free
callback-type snapshot**. `rust/jetstreamer-v0-7-callback-types/src/lib.rs`
states that only callback payload structs required by the offline reducer
are included. That is not a reviewed live HTTP/S3/CAR client.

## Official option space (documentation only)

- Jetstreamer is the selected *initial* V2 acquisition route **iff**
  caller HTTP/S3/backend overrides remain default-deny.
  https://docs.rs/jetstreamer/latest/jetstreamer/
- Official OF1 objects live at `https://files.old-faithful.net/{EPOCH}/`.
  First slice is a slot window, not a full epoch.
  https://docs.old-faithful.net/references/of1-files.md
- Hosted Old Faithful gRPC is not selected and was not probed.

This review did not fetch the upstream Git commit, crates.io, or OF1.
It uses the in-repo snapshot plus previously accessed official docs.

## Verdict

**Acceptable as the initial V2 acquisition crate *candidate* pin.**

**Not acceptable as a blessed live acquisition crate pin.**

The SHA may remain the recorded identity for:

- the vendored callback-type snapshot
- reducer / adapter provenance checks that compare
  `jetstreamer_firehose::UPSTREAM_GIT_SHA` to
  `JETSTREAMER_V0_7_0_GIT_SHA`

It may not be treated as permission to construct a Jetstreamer client,
set `JETSTREAMER_HTTP_BASE_URL` / `ARCHIVE_BASE` / `ARCHIVE_BACKEND` /
S3-shaped overrides, download a CAR, or claim observed OF1 compatibility.

## Why the live pin is still open

1. The in-repo crate is a type snapshot, not the full upstream firehose
   transport.
2. Official Jetstreamer documents caller HTTP/S3/backend overrides. V2
   requires those knobs to fail closed. A snapshot SHA does not implement
   that wrapper.
3. Exact live index/sidecar identities (epoch CID, `slots.txt`, recap,
   GSFA) are not bound to this SHA as an approved acquisition path.
4. Host/redirect allowlisting for a live run is still a per-plan
   parameter. No lease exists.

KNOWN_ISSUES #6 therefore stays **open**: the candidate is reviewed and
named; the live Jetstreamer/OF1 path is still unresolved and
unauthorized.

## Non-claims

- No crate upgrade.
- No network call.
- No change to B4A `networkEnabled`.
- No `RESEARCH_READY` or observed-compatibility claim.
