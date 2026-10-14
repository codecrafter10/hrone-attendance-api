# DECISIONS.md

1. **Indexes:** I created a unique index on `employees.emp_code` to prevent duplicate employee codes, and a unique compound index on `attendance_logs.emp_code` and `date` to prevent duplicate daily attendance records. I also added an index on `date` and `emp_code` for attendance listing and an index on `department` and `joined_on` for department-based queries. I would consider a separate index for status-based filtering if query analysis showed it was needed.

2. **Punch-in race:** Two identical punch-in requests may both check for an existing record before either inserts. The unique compound index allows only one insert to succeed. The losing request receives a conflict response rather than creating a duplicate record.

3. **Ties:** The leaderboard ranks employees by total late minutes and filters by rank, so employees tied at the cutoff can all be included. Results are then sorted deterministically by employee code.

4. **Headcount:** The department summary starts from employees and looks up their attendance records. This includes eligible employees with no attendance logs while excluding employees who joined after the month ends.

5. **Scaling:** For 100 times more data, I would examine query execution statistics and workload patterns, optimize indexes, and consider pagination or precomputed analytics where appropriate.