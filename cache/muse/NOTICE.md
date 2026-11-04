# Committed Muse Spark reply (ADR-020)

`ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad.json` holds the single Muse Spark request and reply behind the intersection 2 experiment (`experiments/records/ngsim-int2-experiment-20261028T075803Z.json`).

- **Why it's committed:** so that a fresh clone can replay that experiment offline. See the README, "From a fresh clone".
- **Approval:** the account holder approved committing it on 2026-10-28.
- **Everything else** in `cache/` stays out of Git (ADR-009).

## What the file contains

- **The request:** one request, sent on 2026-10-28T07:58:03Z to `muse-spark-1.3` with prompt 0.1.0. The file is a `MuseRequestRecord` 0.1.0, holding:
  - the request body, which is the prompt plus the evidence bundle;
  - the raw reply body;
  - the request id, the send time, the elapsed time, the estimated cost and the token usage.
- **Credentials:** none. The API key never appears in a cache record; the adapter scrubs it.
- **File name:** the file name is the request key, the SHA-256 of the canonical request body. Replay uses the file only when the stored body hashes to that key (`MuseClient.fetch`). An edited request is therefore refused, not replayed.
- **Model-generated text:** the reply's hypotheses, proposals, rationales and stated trade-offs are Muse Spark output. They are not evidence, and the report and the app label them as model-generated.
  - **Known slip:** the reply calls arm N's left turners "northbound". Arm N means vehicles arriving from the north, travelling southbound.
  - That convention was later added to the bundle and to prompt 0.2.0. Prompt 0.2.0 has not been sent.
- **No reasoning text:** the reply's reasoning item has an empty summary, and nothing is encrypted.

## Changes from the raw record

The values of the four `x-ratelimit-*` response headers are replaced with `[removed]`. These are the account's request and token limits, and what remained of them when the reply arrived. Nothing else changed, and replay does not read these values.

| | SHA-256 |
|---|---|
| Raw record (kept locally, not committed) | `8924e91cc8215681cacfd0437dc98c9837d41208e2283bf7f1699a70f7e7030f` |
| This file | `2c94eb6f579b2ea02af7b391d17595732e6ea6125f80b3d60b10bcc38804a92d` |

## Licence and attribution

- **Model inputs and outputs:** owned by the account holder under the Meta Model API Terms of Service §4.1 (`docs/HACKATHON_COMPLIANCE.md`, row 8).
- **Derived data:** the request body contains the evidence bundle. Its values are derived from the NGSIM trajectories:

  > U.S. Department of Transportation, Federal Highway Administration, Next Generation Simulation (NGSIM) Lankershim Boulevard vehicle trajectories, data.transportation.gov dataset `8ect-6jqj` (DOI 10.21949/1504477). Licensed under CC BY-SA 3.0 Unported: https://creativecommons.org/licenses/by-sa/3.0/

  Changes: CairoFlow's fixed rules (`cairoflow_perception.ngsim_junction`) aggregated the trajectories into counts, queues, a signal timeline and junction geometry. Those were then summarised into evidence items (`cairoflow_reasoning.evidence`).
- **This file's licence:** CC BY-SA 3.0 Unported, the same as the other committed records derived from the trajectories.
