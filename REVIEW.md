# REVIEW.md

## Review scope and limitation

The original starter version of `app/main.py` could not be recovered from the available project files. Therefore, I cannot reliably attribute specific defects to the original starter or claim that the required defect list is complete. The finding below is a confirmed issue observed in the implementation before the history fix.

## Confirmed finding

| # | Where | What was wrong | How to notice it | Fix and verification |
|---|---|---|---|---|
| 1 | `regularize_attendance`, history `changes` construction | Changes to derived fields (`work_hours`, `late_minutes`, `overtime_minutes`, and `half_day`) were not included in the audit history. | Regularize an attendance record and compare the recalculated values with the latest history entry. | Added comparisons for derived fields before creating the history entry. Verified through the API: changing punch-out from 18:00 to 17:00 changed `work_hours` from 8.42 to 7.42, and both changes appeared in history. |

## Other checks performed

- Unique indexes enforce employee-code uniqueness and one attendance record per employee/date.
- Punch-out uses an atomic update to prevent two concurrent requests from successfully closing the same record.
- The seven local unit tests passed. These tests cover late-minute boundaries, work-hour rounding, overtime, and invalid epoch-millisecond values.
- Python syntax compilation passed. Full hidden-dataset and concurrency testing has not been completed.

## Limitation

Because the original starter is unavailable, I could not establish which other defects were present in its helpers or endpoints, or verify a complete list of original-starter defects.
