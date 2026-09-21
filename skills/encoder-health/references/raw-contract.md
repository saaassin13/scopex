# Raw evidence and optional device profile

The first version detects local signed-rate deviations and persistent rate transitions.
It does not certify slow drift, scale error, physical reversal, or mechanical rebound.
All calculations use raw count differences and actual log intervals; those intervals
are not automatically the hardware acquisition intervals.

`scan --profile PATH` accepts a JSON object maintained from verified device/code
and runtime evidence. It is optional, not something the investigating model should
invent to obtain a confirmed result. Store its source/version in `source`.

| Field | Meaning |
| --- | --- |
| `source` | Required nonempty provenance for every supplied profile |
| `count_min`, `count_max` | Optional inclusive legal range of the interpreted raw count; violation can confirm a raw data anomaly |
| `unsigned_print_bits` | Optional 32 or 64: reinterpret unsigned printed integers as signed two's-complement; use only for verified logging representation |
| `max_rate_counts_s` | Optional verified upper bound for absolute count rate, not an observed percentile |
| `timestamp_uncertainty_ms` | Required with the rate bound; upper bound on each timestamp's error relative to acquisition |
| `count_tolerance` | Optional allowed error of the count difference, in counts; defaults to zero |
| `continuity_verified` | Must be true for a rate bound; profile evidence establishes no unhandled reset/wrap or source change in the analyzed interval |

A rate violation is confirmed only when `(abs(delta)-tolerance)/(dt+2*time_error)`
(with consistent units and a nonnegative numerator) exceeds the supplied bound,
the pair is continuous, and the inspected context has no captured counter/control
event needing explanation. Invalid readings are excluded from motion differences
but retained as range-violation evidence. Local shape thresholds are screening
parameters, not physical bounds and not calibrated fault probabilities.

Raw source caveat: local CowDisinfect `EncoderModbusWorker.cpp` uses an `int64_t`
value with `%zu` in the raw log statement. A large printed integer may represent a
negative value. This source observation is not proof of the deployed version, ABI
or encoder type. Without a verified profile, preserve the printed number and do
not assume negative values or values above `2**63` are invalid. `filtered[...]`
never influences raw validity.

Saved schema 6 contains captured raw lines, file-content hashes, samples, context,
checks and reviewed/unresolved state. `inspect` works after original logs rotate.
Input changes during scan fail rather than silently mixing snapshots. Files must
belong to the same encoder; the known format has no reliable device identity to
automatically separate mixed devices. Use data-locator's single-device source.

The default input limits are 256 MiB of log bytes and 250,000 raw samples; exceeding
them returns an explicit error, not a partial normal result. Clock rollback makes
that source's motion comparisons unavailable; same-time records are retained and
break continuity rather than being silently deduplicated. This conservative choice
may yield unresolved results on noisy/duplicated inputs.

`--motion-report`, legacy schema 3–5 inspection and application-based analysis are
retired. Rerun `scan` to create schema 6. A positional-file call still invokes raw
scan; `--events-out` is an alias of the saved-analysis `--out` path, not old events.
Legacy captured evidence remains historical evidence; no old data is rewritten.
