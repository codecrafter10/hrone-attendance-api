# HROne Employee Attendance & Analytics API

A FastAPI and MongoDB backend for employee management, attendance tracking, attendance regularization, and workforce analytics.

## Features

- Employee creation and paginated employee listing
- Attendance punch-in, punch-out, listing, and regularization
- Attendance calculations based on IST shift rules
- Monthly employee analytics and department summaries
- Late-arrival leaderboard and department attendance trends
- Query execution statistics through the admin explain endpoint
- MongoDB indexes for uniqueness and query performance

## Requirements

- Python 3.11 or newer
- MongoDB 6.0 or newer

## Setup

Create and activate a virtual environment on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Configure the MongoDB environment variables in PowerShell:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "hrone_attendance"
```

Use your actual MongoDB connection URI if MongoDB is running elsewhere.

Start the API:

```powershell
uvicorn app.main:app --port 8000
```

Open the interactive API documentation at:

http://localhost:8000/docs

Health check:

http://localhost:8000/health

## Run Tests

```powershell
python -m pytest -q
```

## Project Files

- `app/main.py` — API endpoints, validation, calculations, and analytics
- `requirements.txt` — Python dependencies
- `tests/` — automated tests
- `DECISIONS.md` — implementation and design decisions
- `REVIEW.md` — starter-code review notes
- `openapi.yaml` — API contract
- `DATA_MODEL.md` — MongoDB data model

## Submission

Do not commit virtual environments, `.env` files, credentials, or other secrets.



Contact Details
Name : Zaid Ali
Email : zaidali.za2635@gmail.com
