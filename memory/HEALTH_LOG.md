# RISEDUAL AI — Health Log

Automated one-line appends from the self-test monitor job (`_run_self_test_monitor`,
runs every 15 minutes). The monitor only writes on state changes (PASS ↔ FAIL) and a
once-per-hour heartbeat so this file stays readable.

Format:
```
<ISO-timestamp> overall=<PASS|FAIL> pass=<n>/<total> fail=<n> [failures=[names]] [# state A → B]
```

Use `/app/scripts/self-test.sh` for an on-demand run with a full table output.
Use the "Run self-test" button in Admin → Developer Tools for the same from the UI.

---

2026-04-20T09:15:13.941318+00:00 overall=PASS pass=6/6 fail=0  # state <PASS|FAIL> → PASS
2026-04-20T12:04:03.200855+00:00 overall=PASS pass=6/6 fail=0
2026-04-20T13:10:05.493565+00:00 overall=PASS pass=6/6 fail=0
2026-04-20T15:12:17.556145+00:00 overall=PASS pass=6/6 fail=0
2026-04-20T16:11:58.651030+00:00 overall=PASS pass=6/6 fail=0
2026-04-20T18:01:09.464524+00:00 overall=PASS pass=6/6 fail=0
2026-04-20T19:01:09.454609+00:00 overall=PASS pass=6/6 fail=0
2026-04-20T21:01:07.970930+00:00 overall=PASS pass=6/6 fail=0
2026-04-21T00:08:47.766343+00:00 overall=PASS pass=6/6 fail=0
2026-04-21T11:01:58.146816+00:00 overall=PASS pass=6/6 fail=0
2026-04-21T12:12:43.391759+00:00 overall=PASS pass=6/6 fail=0
2026-04-21T13:02:51.833890+00:00 overall=PASS pass=6/6 fail=0
2026-04-21T15:10:11.773449+00:00 overall=PASS pass=6/6 fail=0
