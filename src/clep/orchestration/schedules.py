"""Standing orders: what a schedule is, when it is due, and what it runs.

`REQ-F-10-1` requires evaluations to execute on a schedule without human
initiation. The contract has described `cadence` as "a cron expression,
interpreted in UTC" since Phase 3, so that is what this module reads. There is no
second cadence dialect and no default: an expression this parser cannot read is
refused when the schedule is created, because a standing order that silently
never fires is indistinguishable from one that works until someone checks.

Three properties are deliberate.

**UTC, always.** A cadence interpreted in a local zone changes meaning twice a
year, and the runs either side of the change are not comparable in the way their
reader will assume they are.

**Due-ness is a property of the minute, not of elapsed time.** The trigger's
identity is the UTC minute the expression matched, which is what makes a
duplicate trigger harmless: two sweeps in the same minute derive the same
idempotency key, and the second creates no second run. A cadence measured as
"time since the last run" cannot do that — the second sweep would compute a
different elapsed time and fire again.

**Nothing here talks to a queue or a clock it did not receive.** `now` is an
argument, so the eligibility rule is testable without waiting for a wall clock to
reach an interesting value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import psycopg

from clep.identity import actor_uuid, new_ulid, ulid_to_uuid, uuid_to_ulid

#: The subset of `RunTrigger` a standing order may carry. A schedule cannot be
#: `manual` — that is the opposite of what it is — and a pull request is not a
#: schedule. Kept here as well as in the schema because the vocabulary check
#: compares the two.
SCHEDULE_TRIGGERS = ("schedule", "post_deployment", "canary")

#: Only these two describe a system that is already live, so only these two
#: produce a release observation (REQ-F-10-2).
OBSERVING_TRIGGERS = ("post_deployment", "canary")

ACTIVE = "active"
PAUSED = "paused"
SCHEDULE_STATES = (ACTIVE, PAUSED)

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")


class CadenceError(ValueError):
    """Raised for any cadence this module cannot interpret exactly."""


@dataclass(frozen=True)
class Cadence:
    """A parsed five-field cron expression, matched in UTC."""
    expression: str
    minutes: frozenset
    hours: frozenset
    days_of_month: frozenset
    months: frozenset
    days_of_week: frozenset
    #: True when the expression constrains neither day-of-month nor day-of-week,
    #: in which case the two are not combined and every day matches.
    day_unrestricted: bool

    def matches(self, moment: datetime) -> bool:
        """Whether this expression fires in the UTC minute `moment` falls in."""
        moment = _as_utc(moment)
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        if self.day_unrestricted:
            return True
        # Vixie cron's rule: when both day fields are restricted the match is a
        # union, not an intersection. Stated rather than inherited, because the
        # two readings differ on "the 1st and every Monday" and a reader of a
        # release schedule is entitled to know which one they get.
        weekday = (moment.weekday() + 1) % 7  # Python Monday=0 -> cron Sunday=0
        return (moment.day in self.days_of_month) or (weekday in self.days_of_week)


def parse_cadence(expression: str) -> Cadence:
    """Five fields, or an error naming the field that could not be read."""
    if expression is None:
        raise CadenceError("a schedule has no cadence; there is no default")
    fields = expression.split()
    if len(fields) != 5:
        raise CadenceError(
            f"{expression!r} has {len(fields)} field(s); a cadence is five "
            f"fields — minute hour day-of-month month day-of-week — in UTC")
    parsed = [_parse_field(f, low, high, name)
              for f, (low, high), name in zip(fields, _FIELD_RANGES, _FIELD_NAMES)]
    return Cadence(
        expression=expression, minutes=parsed[0], hours=parsed[1],
        days_of_month=parsed[2], months=parsed[3], days_of_week=parsed[4],
        day_unrestricted=fields[2] == "*" and fields[4] == "*")


def _parse_field(field: str, low: int, high: int, name: str) -> frozenset:
    values: set[int] = set()
    for part in field.split(","):
        match = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part)
        if not match:
            raise CadenceError(
                f"{part!r} is not a readable {name} field; supported forms are "
                f"*, n, a-b, and any of those with /step")
        body, step_text = match.group(1), match.group(2)
        step = int(step_text) if step_text else 1
        if step < 1:
            raise CadenceError(f"a step of {step} in the {name} field never fires")
        if body == "*":
            start, stop = low, high
        elif "-" in body:
            start, stop = (int(x) for x in body.split("-"))
        else:
            start = stop = int(body)
        if start < low or stop > high or start > stop:
            raise CadenceError(
                f"{part!r} is outside the {name} range {low}-{high}")
        values.update(range(start, stop + 1, step))
    if not values:
        raise CadenceError(f"the {name} field matches nothing")
    return frozenset(values)


def trigger_key(schedule_id: str, moment: datetime) -> str:
    """The identity of one firing: this schedule, in this UTC minute.

    Derived, never generated. Two sweeps that both see the same due minute
    produce the same key, `create_run` returns the run that already exists, and
    the duplicate trigger costs nothing. A key carrying a sweep number or a
    worker identity would differ between them and the second would be accepted,
    which is the whole failure this prevents.
    """
    return f"schedule:{schedule_id}:{_as_utc(moment).strftime('%Y-%m-%dT%H:%MZ')}"


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise CadenceError(
            "a naive timestamp cannot be matched against a UTC cadence; the "
            "zone it meant would be whatever the host happened to be set to")
    return moment.astimezone(timezone.utc)


@dataclass(frozen=True)
class ScheduleCandidate:
    label: str
    model_configuration_id: str
    prompt_version_id: str | None
    endpoint_kind: str


@dataclass(frozen=True)
class ScheduleRow:
    id: str
    project_id: str
    suite_version_id: str
    gate_policy_version_id: str | None
    baseline_id: str | None
    cadence: str
    trigger_kind: str
    budget_limit: Decimal
    budget_currency: str
    state: str
    last_run_id: str | None
    candidates: tuple = ()

    @property
    def observes_a_release(self) -> bool:
        return self.trigger_kind in OBSERVING_TRIGGERS


class ScheduleError(RuntimeError):
    pass


class ScheduleRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # ------------------------------------------------------------------ writes
    def create_schedule(self, *, project_id: str, suite_version_id: str,
                        cadence: str, budget_limit: Decimal,
                        budget_currency: str, created_by: str,
                        candidates: list[dict],
                        trigger_kind: str = "schedule",
                        gate_policy_version_id: str | None = None,
                        baseline_id: str | None = None) -> str:
        # Parsed before anything is written. A schedule stored with a cadence
        # nothing can read is a row that looks like a standing order and is not
        # one, and it would be discovered by nothing ever happening.
        parse_cadence(cadence)
        if trigger_kind not in SCHEDULE_TRIGGERS:
            raise ScheduleError(
                f"{trigger_kind!r} is not a schedule trigger; a standing order "
                f"is one of {list(SCHEDULE_TRIGGERS)}")
        if not candidates:
            raise ScheduleError(
                "a schedule with no candidates would create runs that evaluate "
                "nothing")
        if budget_limit is None or budget_limit <= 0:
            raise ScheduleError(
                "REQ-F-10-5: a standing order without a positive budget is the "
                "one arrangement where the cost bound cannot apply")
        schedule_id = new_ulid()
        self._conn.execute(
            """
            INSERT INTO clep.evaluation_schedule
                (id, organization_id, project_id, suite_version_id,
                 gate_policy_version_id, baseline_id, cadence, trigger_kind,
                 budget_limit, budget_currency, state, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            """,
            (ulid_to_uuid(schedule_id), self._org, ulid_to_uuid(project_id),
             ulid_to_uuid(suite_version_id),
             ulid_to_uuid(gate_policy_version_id) if gate_policy_version_id else None,
             ulid_to_uuid(baseline_id) if baseline_id else None,
             cadence, trigger_kind, budget_limit, budget_currency,
             actor_uuid(created_by)))
        for index, spec in enumerate(candidates):
            self._conn.execute(
                """
                INSERT INTO clep.evaluation_schedule_candidate
                    (id, organization_id, evaluation_schedule_id, label,
                     model_configuration_id, prompt_version_id, endpoint_kind)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (ulid_to_uuid(new_ulid()), self._org, ulid_to_uuid(schedule_id),
                 spec.get("label") or f"candidate-{index + 1}",
                 ulid_to_uuid(spec["modelConfigurationId"]),
                 ulid_to_uuid(spec["promptVersionId"])
                 if spec.get("promptVersionId") else None,
                 spec.get("endpointKind", "hosted")))
        return schedule_id

    def pause_schedule(self, schedule_id: str) -> bool:
        """Paused, never deleted: what was scheduled survives the decision to
        stop, which is what makes a later reader able to explain a gap."""
        row = self._conn.execute(
            "UPDATE clep.evaluation_schedule SET state = 'paused', "
            "paused_at = now() WHERE organization_id = %s AND id = %s "
            "AND state = 'active' RETURNING id",
            (self._org, ulid_to_uuid(schedule_id))).fetchone()
        return row is not None

    def record_run(self, schedule_id: str, run_id: str) -> None:
        self._conn.execute(
            "UPDATE clep.evaluation_schedule SET last_run_id = %s "
            "WHERE organization_id = %s AND id = %s",
            (ulid_to_uuid(run_id), self._org, ulid_to_uuid(schedule_id)))

    # ------------------------------------------------------------------- reads
    def get_schedule(self, schedule_id: str) -> ScheduleRow | None:
        row = self._conn.execute(
            _SELECT + " WHERE s.organization_id = %s AND s.id = %s",
            (self._org, ulid_to_uuid(schedule_id))).fetchone()
        return self._hydrate(row) if row else None

    def active_schedules(self) -> list[ScheduleRow]:
        rows = self._conn.execute(
            _SELECT + " WHERE s.organization_id = %s AND s.state = 'active' "
                      " ORDER BY s.id", (self._org,)).fetchall()
        return [self._hydrate(r) for r in rows]

    def list_schedules(self, project_id: str) -> list[ScheduleRow]:
        rows = self._conn.execute(
            _SELECT + " WHERE s.organization_id = %s AND s.project_id = %s"
                      " ORDER BY s.id",
            (self._org, ulid_to_uuid(project_id))).fetchall()
        return [self._hydrate(r) for r in rows]

    def due_schedules(self, moment: datetime) -> list[ScheduleRow]:
        """Active schedules whose cadence fires in this UTC minute.

        A cadence that cannot be parsed is not silently skipped: it is refused
        at creation, so reaching this with an unreadable one means the row was
        written around the repository, and raising is the honest response.
        """
        return [s for s in self.active_schedules()
                if parse_cadence(s.cadence).matches(moment)]

    def dataset_version_for(self, suite_version_id: str) -> str | None:
        """The dataset a suite version measures.

        Read from `suite_member` rather than taken from a caller: the suite
        decides what it evaluates, and a scheduler that could choose the dataset
        would be able to change what a standing order means without amending it.
        """
        row = self._conn.execute(
            "SELECT dataset_version_id FROM clep.suite_member "
            "WHERE organization_id = %s AND suite_version_id = %s "
            "ORDER BY dataset_version_id LIMIT 1",
            (self._org, ulid_to_uuid(suite_version_id))).fetchone()
        return uuid_to_ulid(row[0]) if row else None

    # --------------------------------------------------------------- internals
    def _hydrate(self, row) -> ScheduleRow:
        schedule_id = uuid_to_ulid(row[0])
        candidates = self._conn.execute(
            "SELECT label, model_configuration_id, prompt_version_id, "
            "       endpoint_kind FROM clep.evaluation_schedule_candidate "
            "WHERE organization_id = %s AND evaluation_schedule_id = %s "
            "ORDER BY label", (self._org, row[0])).fetchall()
        return ScheduleRow(
            id=schedule_id, project_id=uuid_to_ulid(row[1]),
            suite_version_id=uuid_to_ulid(row[2]),
            gate_policy_version_id=uuid_to_ulid(row[3]) if row[3] else None,
            baseline_id=uuid_to_ulid(row[4]) if row[4] else None,
            cadence=row[5], trigger_kind=row[6], budget_limit=row[7],
            budget_currency=row[8], state=row[9],
            last_run_id=uuid_to_ulid(row[10]) if row[10] else None,
            candidates=tuple(
                ScheduleCandidate(
                    label=c[0], model_configuration_id=uuid_to_ulid(c[1]),
                    prompt_version_id=uuid_to_ulid(c[2]) if c[2] else None,
                    endpoint_kind=c[3])
                for c in candidates))


_SELECT = (
    "SELECT s.id, s.project_id, s.suite_version_id, s.gate_policy_version_id, "
    "       s.baseline_id, s.cadence, s.trigger_kind, s.budget_limit, "
    "       s.budget_currency, s.state, s.last_run_id "
    "FROM clep.evaluation_schedule s")
