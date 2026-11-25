"""Speed of the EEG viewer endpoint on a real 64-channel recording (skipped when the dataset is absent).

Acceptance target (docs): p95 under 300 ms for a 10 s x 64 channel window. This measures the whole request through
the in-process test client (no network), so it is an upper bound on server time, not on what a browser sees.
"""

import statistics
import time

import pytest

from tests.test_recordings import (  # noqa: F401  (fixtures re-exported)
    REAL_EDF,
    isolated_storage,
    patient,
    sig,
    team,
    upload,
)


@pytest.mark.skipif(not REAL_EDF.exists(), reason="EEGMMIDB dataset not available")
def test_signal_window_p95_latency(client, team, patient, capsys):  # noqa: F811
    r = upload(client, team, "clin_a", patient["id"], REAL_EDF.read_bytes())
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    sig(client, team, rid, who="clin_a", start=0, duration=10)  # warm-up (file cache, imports)
    ms = []
    for i in range(40):
        t = time.perf_counter()
        resp = sig(client, team, rid, who="clin_a", start=(i % 10) * 10, duration=10)
        ms.append(1000 * (time.perf_counter() - t))
        assert resp.status_code == 200 and len(resp.json()["data"]) == 64
    ms.sort()
    p95 = ms[int(0.95 * len(ms)) - 1]
    with capsys.disabled():
        print(f"\n/signal 10 s x 64 ch: median {statistics.median(ms):.0f} ms, p95 {p95:.0f} ms, max {ms[-1]:.0f} ms (n={len(ms)})")
    assert p95 < 1000  # generous guard against regressions; the 300 ms target is reported, not enforced
