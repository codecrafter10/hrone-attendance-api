"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
import re
from pydantic import BaseModel, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError


load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
@app.on_event("startup")

def create_indexes():
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index([
        ("department", 1),
        ("joined_on", 1),
    ])

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
    )
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)]
    )



# --------------------------------------------------------------------------- #
# Helpers
def truncate_seconds(value: datetime) -> datetime:
    """Truncate an instant to whole seconds."""
    return value.replace(microsecond=0)


def to_ist(value: datetime) -> datetime:
    """Interpret naive MongoDB datetimes as UTC and convert to IST."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return truncate_seconds(value.astimezone(IST))


def to_mongo_datetime(value: datetime) -> datetime:
    """Convert an instant to UTC-naive datetime for PyMongo."""
    return truncate_seconds(value).astimezone(timezone.utc).replace(
        tzinfo=None
    )

def epoch_ms(value) -> Optional[int]:
    """Convert a datetime or valid epoch-millisecond value to epoch milliseconds."""
    if value is None:
        return None

    if isinstance(value, bool):
        raise TypeError("Boolean is not a valid timestamp")

    if isinstance(value, int):
        if not 100000000000 <= value <= 4102444800000:
            raise ValueError("Timestamp must be in valid epoch milliseconds range")
        return value

    if isinstance(value, float):
        raise TypeError("Float timestamps are not allowed")

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result = int(value.timestamp() * 1000)
        if not 100000000000 <= result <= 4102444800000:
            raise ValueError("Timestamp must be in valid epoch milliseconds range")
        return result
    raise TypeError(f"Unsupported timestamp type: {type(value).__name__}")



def attendance_date(punch_in: datetime, shift_start: str,
                    shift_end: str) -> str:
    """Return the IST date on which the shift started."""
    local = to_ist(punch_in)
    overnight = shift_end <= shift_start

    if overnight and local.strftime("%H:%M") < shift_end:
        local -= timedelta(days=1)

    return local.date().isoformat()


def shift_start_instant(date_str: str, shift_start: str) -> datetime:
    """Build the shift start instant in IST."""
    h, m = map(int, shift_start.split(":"))
    day = datetime.fromisoformat(date_str).date()
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def shift_end_instant(date_str: str, shift_start: str,
                      shift_end: str) -> datetime:
    """Build the shift end instant, advancing a day for overnight shifts."""
    h, m = map(int, shift_end.split(":"))
    day = datetime.fromisoformat(date_str).date()

    if shift_end <= shift_start:
        day += timedelta(days=1)

    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def compute_late_minutes(punch_in: datetime, shift_start: str,
                         date_str: str) -> int:
    local = to_ist(punch_in)
    start = shift_start_instant(date_str, shift_start)
    elapsed = int((local - start).total_seconds())

    # Strictly more than 10 minutes; report whole elapsed minutes.
    return elapsed // 60 if elapsed > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str(
        (punch_out - punch_in).total_seconds()
    ))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(hours)


def compute_overtime(punch_out: datetime, shift_start: str,
                     shift_end: str, date_str: str) -> int:
    local_out = to_ist(punch_out)
    end = shift_end_instant(date_str, shift_start, shift_end)
    elapsed = int((local_out - end).total_seconds())

    minutes = elapsed // 60
    return minutes if minutes >= 30 else 0

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value):
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must be EMP followed by 4-6 digits")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        if not 1 <= len(value) <= 100 or not value.strip():
            raise ValueError("name must contain 1-100 non-blank characters")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        if len(value) > 120 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", value
        ):
            raise ValueError("Invalid email address")
        return value

    @field_validator("department")
    @classmethod
    def validate_department(cls, value):
        if not 1 <= len(value) <= 50 or not value.strip():
            raise ValueError("department must contain 1-50 non-blank characters")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Shift time must use HH:MM in 24-hour format")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
            if parsed.strftime("%Y-%m-%d") != value:
                raise ValueError
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        return value

    @model_validator(mode="after")
    def validate_shift_range(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"
    @field_validator("punched_at", mode="before")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("punched_at must be an integer in milliseconds")
        if not 100000000000 <= value <= 4102444800000:
            raise ValueError("punched_at is outside the valid range")
        return value

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeIn(BaseModel):
    reason: str
    regularized_by: str
    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        db.command("ping")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        )

    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from datetime import date as date_type

    if date_from:
        try:
            date_type.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(422, "Invalid date_from; use YYYY-MM-DD")

    if date_to:
        try:
            date_type.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(422, "Invalid date_to; use YYYY-MM-DD")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if status:
        query["status"] = status

    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to

    total = db.attendance_logs.count_documents(query)

    docs = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    for record in docs:
        record["punch_in"] = epoch_ms(record.get("punch_in"))
        record["punch_out"] = epoch_ms(record.get("punch_out"))
        record.setdefault("work_hours", None)
        record.setdefault("late_minutes", 0)
        record.setdefault("overtime_minutes", 0)
        record.setdefault("half_day", False)

        history = record.get("history") or []
        for entry in history:
            entry["at"] = epoch_ms(entry.get("at"))
            for field in ("punch_in", "punch_out"):
                if field in entry.get("changes", {}):
                    change = entry["changes"][field]
                    change["from"] = epoch_ms(change.get("from"))
                    change["to"] = epoch_ms(change.get("to"))

        record["history"] = history

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }





@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        if body.punched_at is not None:
            instant = datetime.fromtimestamp(
                body.punched_at / 1000, tz=timezone.utc
            )
        else:
            instant = datetime.now(timezone.utc)

        instant = truncate_seconds(instant)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(
            status_code=422, detail="Invalid punched_at timestamp"
        )

    date_str = attendance_date(
        instant, emp["shift_start"], emp["shift_end"]
    )
    instant_db = to_mongo_datetime(instant)

    existing = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": date_str,
    })
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already exists for this date",
        )

    late_minutes = compute_late_minutes(
        instant, emp["shift_start"], date_str
    )

    record = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": instant_db,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(record)
    except Exception:
        # Handles a concurrent request hitting the unique index.
        if db.attendance_logs.find_one({
            "emp_code": body.emp_code,
            "date": date_str,
        }):
            raise HTTPException(
                status_code=409,
                detail="Attendance record already exists for this date",
            )
        raise
    record["punch_in"] = epoch_ms(instant)
    record.pop("_id", None)
    return record


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    from pymongo import ReturnDocument

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        if body.punched_at is not None:
            instant = datetime.fromtimestamp(
                body.punched_at / 1000, tz=timezone.utc
            )
        else:
            instant = datetime.now(timezone.utc)

        instant = truncate_seconds(instant)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(status_code=422, detail="Invalid punched_at timestamp")

    instant_db = to_mongo_datetime(instant)

    # Find the most recent punch-in at or before the punch-out time.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": instant_db},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(status_code=404, detail="No punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Already punched out")

    punch_in_dt = record["punch_in"]
    if punch_in_dt.tzinfo is None:
        punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)
    else:
        punch_in_dt = punch_in_dt.astimezone(timezone.utc)

    elapsed = (instant - punch_in_dt).total_seconds()

    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    work_hours = compute_work_hours(punch_in_dt, instant)
    overtime_minutes = compute_overtime(
        instant,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )
    half_day = work_hours < 4.50

    # Atomic update: only one simultaneous request can close the record.
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": instant_db,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        raise HTTPException(status_code=409, detail="Already punched out")

    # Convert BSON datetimes to epoch milliseconds for the HTTP response.
    updated.pop("_id", None)
    updated["punch_in"] = epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = epoch_ms(updated.get("punch_out"))
    updated.setdefault("history", [])
    updated.setdefault("late_minutes", 0)
    updated.setdefault("overtime_minutes", 0)
    updated.setdefault("half_day", False)

    return updated


# --------------------------------------------------------------------------- #


from calendar import monthrange
from datetime import date as date_type
from decimal import Decimal, ROUND_HALF_UP


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    from pymongo import ReturnDocument
    from datetime import date as date_type

    if len(body.reason.strip()) < 5 or len(body.reason) > 200:
        raise HTTPException(
           status_code=422,
           detail="Reason must be between 5 and 200 characters",
        )
    if not body.regularized_by.strip() or len(body.regularized_by) > 50:
        raise HTTPException(
            status_code=422,
            detail="regularized_by must be between 1 and 50 characters",
        )

    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date; use YYYY-MM-DD")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })

    if not record:
        if not db.employees.find_one({"emp_code": emp_code}):
            raise HTTPException(status_code=404, detail="Employee not found")
        raise HTTPException(status_code=404, detail="Attendance record not found")

    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    old_status = record.get("status")
    new_status = body.status if body.status is not None else old_status

    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    def parse_timestamp(value, field):
        if value is None:
            return None
        try:
            result = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(status_code=422, detail=f"Invalid {field} timestamp")
        return to_mongo_datetime(result)

    new_in = parse_timestamp(body.punch_in, "punch_in") if body.punch_in is not None else old_in
    new_out = parse_timestamp(body.punch_out, "punch_out") if body.punch_out is not None else old_out

    # Non-working statuses must not retain punch times.
    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="Punch times cannot be supplied for ABSENT or LEAVE",
            )
        new_in = None
        new_out = None

    # Validate timestamps for statuses that represent attendance.
    if new_status in ("PRESENT", "WFH", "ON_DUTY"):
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="A punch_in is required for attendance statuses",
            )

        local_in = to_ist(new_in)
        attendance_day = date_type.fromisoformat(date)
        shift_start = employee["shift_start"]
        shift_end = employee["shift_end"]

        # For overnight shifts, early-morning punches belong to the prior day.
        expected_day = local_in.date()
        if shift_end <= shift_start and local_in.time() < datetime.strptime(
            shift_end, "%H:%M"
        ).time():
            expected_day -= timedelta(days=1)

        if expected_day != attendance_day:
            raise HTTPException(
                status_code=422,
                detail="punch_in must fall on the attendance date in IST",
            )

        if new_out is not None:
            if new_out <= new_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )
            if (new_out - new_in).total_seconds() > 24 * 60 * 60:
                raise HTTPException(
                    status_code=422,
                    detail="Punch interval cannot exceed 24 hours",
                )

    changes = {}
    if new_status != old_status:
        changes["status"] = {"from": old_status, "to": new_status}

    for field, old_value, new_value in (
        ("punch_in", old_in, new_in),
        ("punch_out", old_out, new_out),
    ):
        if old_value is not None and old_value.tzinfo is None:
            old_value = old_value.replace(tzinfo=timezone.utc)
        if new_value is not None and new_value.tzinfo is None:
            new_value = new_value.replace(tzinfo=timezone.utc)

        old_ms = epoch_ms(old_value)
        new_ms = epoch_ms(new_value)
        if old_ms != new_ms:
            changes[field] = {"from": old_ms, "to": new_ms}

    if not changes:
        raise HTTPException(status_code=422, detail="No changes supplied")

    updates = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
    }

    if new_status in ("ABSENT", "LEAVE"):
        updates.update({
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        })
    else:
        updates["late_minutes"] = compute_late_minutes(
            new_in, employee["shift_start"], date
        )
        if new_out is not None:
            updates["work_hours"] = compute_work_hours(new_in, new_out)
            updates["overtime_minutes"] = compute_overtime(
                new_out,
                employee["shift_start"],
                employee["shift_end"],
                date,
            )
            updates["half_day"] = updates["work_hours"] < 4.50
        else:
            updates["work_hours"] = None
            updates["overtime_minutes"] = 0
            updates["half_day"] = False

    # Include every changed derived field in the audit history.
    for field in ("work_hours", "late_minutes", "overtime_minutes", "half_day"):
        old_value = record.get(field)
        new_value = updates[field]

        if field in ("late_minutes", "overtime_minutes"):
            old_value = 0 if old_value is None else old_value
        elif field == "half_day":
            old_value = False if old_value is None else old_value

        if old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}
    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by.strip(),
        "reason": body.reason.strip(),
        "changes": changes,
    }

    # Atomic update protects against two requests overwriting the same version.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": old_status,
            "punch_in": old_in,
            "punch_out": old_out,
        },
        {
            "$set": updates,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    updated = db.attendance_logs.find_one(
        {
            "emp_code": emp_code,
            "date": date,
        },
        {"_id": 0},
    )

    if updated is None:
        raise HTTPException(
            status_code=500,
            detail="Attendance update completed but record could not be retrieved",
        )

    updated["punch_in"] = epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = epoch_ms(updated.get("punch_out"))

    updated["history"] = [
        {
            **entry,
            "at": epoch_ms(entry.get("at")),
        }
        for entry in updated.get("history", [])
    ]

    return updated

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    # Validate the requested month.
    try:
        year, month_number = map(int, month.split("-"))
        first_day = date_type(year, month_number, 1)
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    last_day_number = monthrange(year, month_number)[1]
    last_day = date_type(year, month_number, last_day_number)

    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    # Calculate working days in MongoDB, starting from the later
    # of the employee's joining date and the month's first day.
    joined_on = date_type.fromisoformat(employee["joined_on"])
    start_day = max(first_day, joined_on)

    working_days_pipeline = [
        {
            "$documents": [
                {
                    "start": start_day.isoformat(),
                    "end": last_day.isoformat(),
                }
            ]
        },
        {
            "$project": {
                "dates": {
                    "$map": {
                        "input": {
                            "$range": [
                                0,
                                {
                                    "$add": [
                                        {
                                            "$dateDiff": {
                                                "startDate": {
                                                    "$dateFromString": {
                                                        "dateString": "$start",
                                                        "format": "%Y-%m-%d",
                                                    }
                                                },
                                                "endDate": {
                                                    "$dateFromString": {
                                                        "dateString": "$end",
                                                        "format": "%Y-%m-%d",
                                                    }
                                                },
                                                "unit": "day",
                                            }
                                        },
                                        1,
                                    ]
                                },
                            ]
                        },
                        "as": "offset",
                        "in": {
                            "$dateAdd": {
                                "startDate": {
                                    "$dateFromString": {
                                        "dateString": "$start",
                                        "format": "%Y-%m-%d",
                                    }
                                },
                                "unit": "day",
                                "amount": "$$offset",
                            }
                        },
                    }
                }
            }
        },
        {"$unwind": "$dates"},
        {
            "$match": {
                "$expr": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$dates"},
                                [1, 7],
                            ]
                        }
                    ]
                }
            }
        },
        {"$count": "working_days"},
    ]

    working_days_result = list(
        db.command(
            "aggregate",
            1,
            pipeline=working_days_pipeline,
            cursor={},
        )["cursor"]["firstBatch"]
    )
    working_days = (
        working_days_result[0]["working_days"]
        if working_days_result
        else 0
    )

    # Analytics use stored derived attendance values.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_day.isoformat(),
                    "$lte": last_day.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {
                                        "$in": [
                                            {"$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$date",
                                                    "format": "%Y-%m-%d",
                                                }
                                            }},
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    stats = result[0] if result else {}

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(stats.get("present_days", 0)))
             / Decimal(working_days) * Decimal("100"))
            .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": stats.get("present_days", 0),
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }









# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = Query(None),
):
    # Validate the requested month.
    try:
        year, month_number = map(int, month.split("-"))
        first_day = date_type(year, month_number, 1)
        last_day = date_type(
            year, month_number, monthrange(year, month_number)[1]
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be YYYY-MM",
        )

    first_day_str = first_day.isoformat()
    last_day_str = last_day.isoformat()

    # Include every employee who has joined by the end of the month.
    employee_match = {"joined_on": {"$lte": last_day_str}}
    if department is not None:
        employee_match["department"] = department

    presence_statuses = ["PRESENT", "WFH", "ON_DUTY"]

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {"$gte": ["$date", first_day_str]},
                                    {"$lte": ["$date", last_day_str]},
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$status",
                                                        presence_statuses,
                                                    ]
                                                },
                                                {
                                                    "$in": [
                                                        {
                                                            "$dayOfWeek": {
                                                                "$dateFromString": {
                                                                    "dateString": "$date",
                                                                    "format": "%Y-%m-%d",
                                                                }
                                                            }
                                                        },
                                                        [2, 3, 4, 5, 6],
                                                    ]
                                                },
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {
                                                    "$eq": [
                                                        {
                                                            "$ifNull": [
                                                                "$half_day",
                                                                False,
                                                            ]
                                                        },
                                                        True,
                                                    ]
                                                },
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },
                            "work_hours_sum": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$status",
                                                        presence_statuses,
                                                    ]
                                                },
                                                {
                                                    "$ne": [
                                                        {
                                                            "$ifNull": [
                                                                "$work_hours",
                                                                None,
                                                            ]
                                                        },
                                                        None,
                                                    ]
                                                },
                                            ]
                                        },
                                        "$work_hours",
                                        0,
                                    ]
                                }
                            },
                            "work_hours_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$status",
                                                        presence_statuses,
                                                    ]
                                                },
                                                {
                                                    "$ne": [
                                                        {
                                                            "$ifNull": [
                                                                "$work_hours",
                                                                None,
                                                            ]
                                                        },
                                                        None,
                                                    ]
                                                },
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {
                                                    "$ifNull": [
                                                        "$late_minutes",
                                                        0,
                                                    ]
                                                },
                                                0,
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "total_late_minutes": {
                                "$sum": {"$ifNull": ["$late_minutes", 0]}
                            },
                            "leave_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "LEAVE"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "on_duty_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "ON_DUTY"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "month_stats",
            }
        },
        {
            "$unwind": {
                "path": "$month_stats",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$month_stats.present_days"},
                "work_hours_sum": {
                    "$sum": "$month_stats.work_hours_sum"
                },
                "work_hours_count": {
                    "$sum": "$month_stats.work_hours_count"
                },
                "late_count": {"$sum": "$month_stats.late_count"},
                "total_late_minutes": {
                    "$sum": "$month_stats.total_late_minutes"
                },
                "leave_count": {"$sum": "$month_stats.leave_count"},
                "on_duty_count": {"$sum": "$month_stats.on_duty_count"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$divide": [
                                "$work_hours_sum",
                                "$work_hours_count",
                            ]
                        },
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    # Apply the required half-up rounding to each reported average.
    for item in items:
        value = item["avg_work_hours"]
        if value is not None:
            item["avg_work_hours"] = float(
                Decimal(str(value)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )

    return {"month": month, "items": items}



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = Query(None),
):
    try:
        year, month_number = map(int, month.split("-"))
        first_day = date_type(year, month_number, 1)
        last_day = date_type(
            year,
            month_number,
            monthrange(year, month_number)[1],
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid month; use YYYY-MM",
        )

    first_day_str = first_day.isoformat()
    last_day_str = last_day.isoformat()

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": first_day_str,
                    "$lte": last_day_str,
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    from datetime import date as date_type

    # Validate dates.
    try:
        start = date_type.fromisoformat(from_date)
        end = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD",
        )

    if start.isoformat() != from_date or end.isoformat() != to_date:
        raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD")

    day_count = (end - start).days + 1
    if day_count <= 0 or day_count > 92:
        raise HTTPException(
            status_code=422,
            detail="to must be on or after from and the range cannot exceed 92 days",
        )

    employee_count = db.employees.count_documents({"department": department})
    if employee_count == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    # Build a daily series from attendance records, then fill missing dates.
    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start.isoformat(),
                    "$lte": end.isoformat(),
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateFromString": {
                        "dateString": "$_id",
                        "format": "%Y-%m-%d",
                    }
                },
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        datetime.combine(start, datetime.min.time()),
                        datetime.combine(end + timedelta(days=1), datetime.min.time()),
                    ],
                },
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "date_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                    }
                },
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$date"},
                                [1, 7],
                            ]
                        }
                    ]
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0,
                    ]
                },
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": [
                                    {
                                        "$ifNull": [
                                            {"$arrayElemAt": ["$headcount_result.count", 0]},
                                            0,
                                        ]
                                    },
                                    0,
                                ]},
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$present_count",
                                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                                    ]
                                },
                                4,
                            ]
                        },
                        None,
                    ]
                },
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    # Round moving averages to four decimal places, half-up.
    for item in items:
        for field in ("attendance_rate", "moving_avg_7d"):
            if item.get(field) is not None:
                item[field] = float(
                    Decimal(str(item[field])).quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP,
                    )
                )

    return {"department": department, "items": items}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date: Optional[str] = None,
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported endpoint")

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status

        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

        explain = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month=YYYY-MM are required",
            )

        try:
            year, month_number = map(int, month.split("-"))
            first_day = date_type(year, month_number, 1)
            last_day = date_type(
                year, month_number, monthrange(year, month_number)[1]
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="month must be YYYY-MM")

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": first_day.isoformat(),
                        "$lte": last_day.isoformat(),
                    },
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {
                                            "$in": [
                                                {
                                                    "$dayOfWeek": {
                                                        "$dateFromString": {
                                                            "dateString": "$date",
                                                            "format": "%Y-%m-%d",
                                                        }
                                                    }
                                                },
                                                [2, 3, 4, 5, 6],
                                            ]
                                        },
                                    ]
                                },
                                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                                0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                    },
                    "late_count": {
                        "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                    },
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "total_overtime_minutes": {"$sum": "$overtime_minutes"},
                }
            },
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }


    if endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month=YYYY-MM is required",
            )

        try:
            year, month_number = map(int, month.split("-"))
            first_day = date_type(year, month_number, 1)
            last_day = date_type(
                year,
                month_number,
                monthrange(year, month_number)[1],
            )
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must be YYYY-MM",
            )

        first_day_str = first_day.isoformat()
        last_day_str = last_day.isoformat()

        employee_match = {"joined_on": {"$lte": last_day_str}}
        if department is not None:
            employee_match["department"] = department

        presence_statuses = ["PRESENT", "WFH", "ON_DUTY"]

        pipeline = [
            {"$match": employee_match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"employee_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$emp_code",
                                                "$$employee_code",
                                            ]
                                        },
                                        {"$gte": ["$date", first_day_str]},
                                        {"$lte": ["$date", last_day_str]},
                                    ]
                                }
                            }
                        },
                        {
                            "$group": {
                                "_id": None,
                                "present_days": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    {"$in": ["$status", presence_statuses]},
                                                    {
                                                        "$in": [
                                                            {
                                                                "$dayOfWeek": {
                                                                    "$dateFromString": {
                                                                        "dateString": "$date",
                                                                        "format": "%Y-%m-%d",
                                                                    }
                                                                }
                                                            },
                                                            [2, 3, 4, 5, 6],
                                                        ]
                                                    },
                                                ]
                                            },
                                            {
                                                "$cond": [
                                                    {"$eq": [{"$ifNull": ["$half_day", False]}, True]},
                                                    0.5,
                                                    1,
                                                ]
                                            },
                                            0,
                                        ]
                                    }
                                },
                                "work_hours_sum": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    {"$in": ["$status", presence_statuses]},
                                                    {"$ne": [{"$ifNull": ["$work_hours", None]}, None]},
                                                ]
                                            },
                                            "$work_hours",
                                            0,
                                        ]
                                    }
                                },
                                "work_hours_count": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    {"$in": ["$status", presence_statuses]},
                                                    {"$ne": [{"$ifNull": ["$work_hours", None]}, None]},
                                                ]
                                            },
                                            1,
                                            0,
                                        ]
                                    }
                                },
                                "late_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                                "total_late_minutes": {
                                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                                },
                                "leave_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$status", "LEAVE"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                                "on_duty_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$status", "ON_DUTY"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                            }
                        },
                    ],
                    "as": "month_stats",
                }
            },
            {
                "$unwind": {
                    "path": "$month_stats",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$group": {
                    "_id": "$department",
                    "headcount": {"$sum": 1},
                    "present_days": {"$sum": "$month_stats.present_days"},
                    "work_hours_sum": {"$sum": "$month_stats.work_hours_sum"},
                    "work_hours_count": {"$sum": "$month_stats.work_hours_count"},
                    "late_count": {"$sum": "$month_stats.late_count"},
                    "total_late_minutes": {"$sum": "$month_stats.total_late_minutes"},
                    "leave_count": {"$sum": "$month_stats.leave_count"},
                    "on_duty_count": {"$sum": "$month_stats.on_duty_count"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": 1,
                    "present_days": 1,
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": ["$work_hours_count", 0]},
                            {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                            None,
                        ]
                    },
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain,
        }

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month=YYYY-MM is required",
            )

        try:
            year, month_number = map(int, month.split("-"))
            first_day = date_type(year, month_number, 1)
            last_day = date_type(
                year, month_number, monthrange(year, month_number)[1]
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="month must be YYYY-MM")

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": first_day.isoformat(),
                        "$lte": last_day.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }
            },
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }

    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="department, from=YYYY-MM-DD and to=YYYY-MM-DD are required",
            )

        try:
            start = date_type.fromisoformat(from_date)
            end = date_type.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="from and to must use YYYY-MM-DD",
            )
        if (
            start.isoformat() != from_date
            or end.isoformat() != to_date
            or start > end
            or (end - start).days + 1 > 92
        ):
            raise HTTPException(
                status_code=422,
                detail="Invalid date range; maximum range is 92 days",
            )


        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": from_date,
                        "$lte": to_date,
                    }
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
            {"$match": {"employee.department": department}},
            {
                "$group": {
                    "_id": "$date",
                    "present_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        "$status",
                                        ["PRESENT", "WFH", "ON_DUTY"],
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": ["$late_minutes", 0]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": {
                        "$dateFromString": {
                            "dateString": "$_id",
                            "format": "%Y-%m-%d",
                        }
                    },
                    "present_count": 1,
                    "late_count": 1,
                }
            },
            {
                "$densify": {
                    "field": "date",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [
                            datetime.combine(start, datetime.min.time()),
                            datetime.combine(
                                end + timedelta(days=1),
                                datetime.min.time(),
                            ),
                        ],
                    },
                }
            },
            {
                "$set": {
                    "present_count": {"$ifNull": ["$present_count", 0]},
                    "late_count": {"$ifNull": ["$late_count", 0]},
                    "date_string": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$date",
                        }
                    },
                    "is_working_day": {
                        "$not": [
                            {
                                "$in": [
                                    {"$dayOfWeek": "$date"},
                                    [1, 7],
                                ]
                            }
                        ]
                    },
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"day": "$date_string"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$department",
                                                department,
                                            ]
                                        },
                                        {"$lte": ["$joined_on", "$$day"]},
                                    ]
                                }
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "headcount_result",
                }
            },
            {
                "$set": {
                    "headcount": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$headcount_result.count", 0]},
                            0,
                        ]
                    },
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {
                                        "$gt": [
                                            {
                                                "$ifNull": [
                                                    {
                                                        "$arrayElemAt": [
                                                            "$headcount_result.count",
                                                            0,
                                                        ]
                                                    },
                                                    0,
                                                ]
                                            },
                                            0,
                                        ]
                                    },
                                ]
                            },
                            {
                                "$round": [
                                    {
                                        "$divide": [
                                            "$present_count",
                                            {
                                                "$arrayElemAt": [
                                                    "$headcount_result.count",
                                                    0,
                                                ]
                                            },
                                        ]
                                    },
                                    4,
                                ]
                            },
                            None,
                        ]
                    },
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"date": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]},
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$date_string",
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": 1,
                }
            },
            {"$sort": {"date": 1}},
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }

    raise HTTPException(status_code=422, detail="Unsupported endpoint")
