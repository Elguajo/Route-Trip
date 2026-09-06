from hashlib import md5, sha256
from io import BytesIO
import json
from typing import Annotated

from fastapi import (APIRouter, Depends, File, HTTPException, Request,
                     Response, UploadFile)
from fastapi.responses import FileResponse
from sqlalchemy import update
from sqlalchemy.orm import selectinload
from sqlmodel import select

from ..config import get_settings
from ..deps import SessionDep, get_current_username
from ..models.models import (Image, ItemImageInput,
                             NotificationChecklistItemRead, Place, Trip,
                             TripAttachment, TripAttachmentRead,
                             TripBalanceEntry, TripBooking,
                             TripCalendarDetails, TripChecklist,
                             TripChecklistCreate, TripChecklistEntry,
                             TripChecklistEntryCreate, TripChecklistEntryRead,
                             TripChecklistEntryUpdate, TripChecklistItem,
                             TripChecklistItemCreate, TripChecklistItemRead,
                             TripChecklistItemUpdate, TripChecklistRead,
                             TripChecklistUpdate, TripCreate, TripDay,
                             TripDayBase, TripDayRead, TripInvitationRead,
                             TripItem, TripItemCreate, TripItemRead,
                             TripItemUpdate, TripMember, TripMemberCreate,
                             TripMemberRead, TripPackingList,
                             TripPackingListCreate, TripPackingListEntry,
                             TripPackingListEntryCreate,
                             TripPackingListEntryRead,
                             TripPackingListEntryUpdate, TripPackingListItem,
                             TripPackingListItemCreate,
                             TripPackingListItemRead,
                             TripPackingListItemUpdate, TripPackingListRead,
                             TripPackingListUpdate, TripRead, TripReadBase,
                             TripPlannerSettings, TripPlannerSettingsRead,
                             TripPlannerSettingsUpdate,
                             TripShare, TripShareCreate, TripShareDetails,
                             TripShareRead, TripUpdate, User)
from ..optimization import (DayItemSnapshot, DayManualReorderRequest, DayOptimizationApplyRequest,
                            DayOptimizationApplyResult,
                            DayOptimizationPreviewRequest,
                            DayOptimizationResult, OSRMTableRoutingProvider,
                            TravelMatrixCache, TripAllocator, TripOptimizationApplyRequest,
                            TripOptimizationApplyResult, TripOptimizationPreviewResult,
                            TripPlanningSettings, TripPlanningSnapshotAssignment,
                            TripPlanningTotals, TripOptimizer)
from ..utils.date import dt_utc
from ..utils.ical import build_trip_ics, ics_filename
from ..utils.link_titles import resolve_links
from ..utils.utils import (attachments_trip_folder_path, b64img_decode,
                           generate_urlsafe, remove_image, save_attachment,
                           save_image_to_file)
from ..utils.zip import zip_trip_attachments

router = APIRouter(prefix="/api/trips", tags=["trips"])


# Planner results are derived from the persisted day snapshot and are never
# stored. Reusing a bounded process-local cache avoids duplicate Table calls
# without introducing a provider fallback or a database cache.
_day_matrix_cache = TravelMatrixCache()


def _next_trip_item_sequence(session: SessionDep, day_id: int) -> int:
    latest_sequence = session.exec(
        select(TripItem.sequence)
        .where(TripItem.day_id == day_id)
        .order_by(TripItem.sequence.desc())
        .limit(1)
    ).first()
    return latest_sequence + 1 if latest_sequence is not None else 0


def _get_trip_day_or_404(session: SessionDep, trip_id: int, day_id: int) -> TripDay:
    day = session.get(TripDay, day_id)
    if not day or day.trip_id != trip_id:
        raise HTTPException(status_code=404, detail="Not found")
    return day


def _ordered_day_items(session: SessionDep, day_id: int) -> list[TripItem]:
    return list(
        session.exec(
            select(TripItem)
            .where(TripItem.day_id == day_id)
            .order_by(TripItem.sequence, TripItem.id)
        )
    )


def _day_optimizer_for_user(session: SessionDep, current_user: str) -> TripOptimizer:
    user = session.get(User, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    provider = OSRMTableRoutingProvider(user.map_provider.value, cache=_day_matrix_cache)
    return TripOptimizer(provider)


def _trip_allocator_for_user(session: SessionDep, current_user: str) -> TripAllocator:
    """Use the same authenticated selected-provider boundary as day planning."""

    user = session.get(User, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return TripAllocator(OSRMTableRoutingProvider(user.map_provider.value, cache=_day_matrix_cache))


def _ordered_trip_days(session: SessionDep, trip_id: int) -> list[TripDay]:
    return list(
        session.exec(
            select(TripDay)
            .where(TripDay.trip_id == trip_id)
            .order_by(TripDay.dt.asc().nulls_last(), TripDay.label, TripDay.id)
        )
    )


def _eligible_trip_planning_items(session: SessionDep, trip_id: int) -> list[TripItem]:
    """Return only POI-backed items; generic itinerary entries stay untouched.

    A POI may lack usable coordinates, but it remains eligible and is carried by
    ``TripAllocator`` with its existing coordinate-less diagnostic.
    """

    return list(
        session.exec(
            select(TripItem)
            .join(TripDay)
            .options(selectinload(TripItem.place))
            .where(TripDay.trip_id == trip_id, TripItem.place_id.is_not(None))
            .order_by(TripItem.day_id, TripItem.sequence, TripItem.id)
        )
    )


def _planning_item_snapshots(items: list[TripItem]) -> tuple[DayItemSnapshot, ...]:
    """Prefer item coordinates, then the linked POI's coordinates for planning."""

    return tuple(
        DayItemSnapshot(
            item_id=item.id,
            sequence=item.sequence,
            lat=item.lat if item.lat is not None else item.place.lat if item.place else None,
            lng=item.lng if item.lng is not None else item.place.lng if item.place else None,
        )
        for item in items
    )


def _planning_assignments(items: list[TripItem]) -> tuple[TripPlanningSnapshotAssignment, ...]:
    return tuple(
        TripPlanningSnapshotAssignment(item_id=item.id, day_id=item.day_id, sequence=item.sequence)
        for item in items
    )


def _planning_snapshot_token(
    settings: TripPlanningSettings,
    assignments: tuple[TripPlanningSnapshotAssignment, ...],
    target_day_ids: tuple[int | None, ...],
    routing_provider: str,
) -> str:
    """Bind apply to every persisted input that can change this proposal."""

    payload = {
        "settings": settings.model_dump(mode="json"),
        "assignments": [assignment.model_dump(mode="json") for assignment in assignments],
        "target_day_ids": target_day_ids,
        "routing_provider": routing_provider,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _planning_totals(allocation) -> TripPlanningTotals | None:
    comparisons = tuple(day.optimization.cost_comparison for day in allocation.days)
    if any(comparison is None for comparison in comparisons):
        return None
    comparisons = tuple(comparison for comparison in comparisons if comparison is not None)
    starting_distances = tuple(comparison.starting.distance_m for comparison in comparisons)
    optimized_distances = tuple(comparison.optimized.distance_m for comparison in comparisons)
    return TripPlanningTotals(
        starting_duration_s=sum(comparison.starting.duration_s for comparison in comparisons),
        optimized_duration_s=sum(comparison.optimized.duration_s for comparison in comparisons),
        starting_distance_m=None if any(distance is None for distance in starting_distances) else sum(starting_distances),
        optimized_distance_m=None if any(distance is None for distance in optimized_distances) else sum(optimized_distances),
    )


async def _whole_trip_preview(
    session: SessionDep, trip_id: int, current_user: str
) -> tuple[TripOptimizationPreviewResult, list[TripItem]]:
    settings_row = session.get(TripPlannerSettings, trip_id) or TripPlannerSettings(trip_id=trip_id)
    settings = TripPlanningSettings.from_persisted(settings_row)
    user = session.get(User, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    items = _eligible_trip_planning_items(session, trip_id)
    assignments = _planning_assignments(items)
    existing_day_ids = tuple(day.id for day in _ordered_trip_days(session, trip_id))
    target_day_ids = tuple(
        existing_day_ids[index] if index < len(existing_day_ids) else None
        for index in range(settings.requested_days)
    )
    allocation = await _trip_allocator_for_user(session, current_user).allocate(
        settings, _planning_item_snapshots(items)
    )
    return (
        TripOptimizationPreviewResult(
            starting_assignments=assignments,
            snapshot_token=_planning_snapshot_token(
                settings, assignments, target_day_ids, user.map_provider.value
            ),
            target_day_ids=target_day_ids,
            allocation=allocation,
            totals=_planning_totals(allocation),
        ),
        items,
    )


def _trip_from_token_or_404(session, token: str) -> TripShare:
    share = session.exec(select(TripShare).where(TripShare.token == token)).first()
    if not share:
        raise HTTPException(status_code=404, detail="Not found")
    return share


def _trip_usernames(session, trip_id: int) -> set[str]:
    owner = session.exec(select(Trip.user).where(Trip.id == trip_id)).first()
    members = session.exec(
        select(TripMember.user).where(TripMember.trip_id == trip_id, TripMember.joined_at.is_not(None))
    ).all()
    return {owner} | set(members)


def _get_verified_trip(session, trip_id: int, username: str) -> Trip:
    # Merge of _verify_trip_member(+_can_access_trip) + _get_trip_or_404
    # Returns a Trip if: it exists and username is a TripMember or trip.user (owner)
    trip = session.exec(
        select(Trip)
        .outerjoin(TripMember)
        .where(
            Trip.id == trip_id,
            (Trip.user == username) | ((TripMember.user == username) & (TripMember.joined_at.is_not(None))),
        )
    ).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Not found")
    return trip


def _get_own_list_or_404(session, model: type, list_id: int, trip_id: int):
    # Fetches a TripPackingList/TripChecklist row scoped to the given trip, or 404s.
    obj = session.exec(select(model).where(model.id == list_id, model.trip_id == trip_id)).one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


def _get_own_entry_or_404(
    session,
    entry_model: type,
    parent_model: type,
    fk_column,
    item_id: int,
    list_id: int,
    trip_id: int,
):
    # Fetches a TripPackingListEntry/TripChecklistEntry row scoped to the given list+trip, or 404s.
    obj = session.exec(
        select(entry_model)
        .join(parent_model)
        .where(
            entry_model.id == item_id,
            fk_column == list_id,
            parent_model.trip_id == trip_id,
        )
    ).one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


def _trip_for_ics(session, *where) -> Trip:
    # Eager-loads exactly what build_trip_ics touches, so rendering a feed stays
    # a single round trip no matter how many days/items the trip has.
    trip = session.exec(
        select(Trip)
        .options(selectinload(Trip.days).selectinload(TripDay.items).selectinload(TripItem.place))
        .where(*where)
    ).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Not found")
    return trip


def _ics_download(trip: Trip) -> Response:
    return Response(
        content=build_trip_ics(trip),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{ics_filename(trip)}"'},
    )


@router.get("", response_model=list[TripReadBase])
def read_trips(
    session: SessionDep, current_user: Annotated[str, Depends(get_current_username)]
) -> list[TripReadBase]:
    trips = session.exec(
        select(Trip)
        .join(TripMember, isouter=True)
        .where(
            (Trip.user == current_user)
            | ((TripMember.user == current_user) & (TripMember.joined_at.is_not(None)))
        )
        .distinct()
    )
    return [TripReadBase.serialize(trip) for trip in trips]


@router.get("/invitations", response_model=list[TripInvitationRead])
def read_pending_invitations(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[TripInvitationRead]:
    pending_inviattions = session.exec(
        select(TripMember, Trip)
        .join(Trip, Trip.id == TripMember.trip_id)
        .where(
            TripMember.user == current_user,
            TripMember.joined_at.is_(None),
        )
    ).all()

    invitations: list[TripInvitationRead] = []
    for tm, trip in pending_inviattions:
        base = TripReadBase.serialize(trip)
        invitations.append(
            TripInvitationRead(
                **base.model_dump(),
                invited_by=tm.invited_by,
                invited_at=tm.invited_at,
            )
        )

    return invitations


@router.get("/invitations/pending", response_model=bool)
def has_pending_invitations(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> bool:
    pending = session.exec(
        select(TripMember.id).where(TripMember.user == current_user, TripMember.joined_at.is_(None)).limit(1)
    ).first()
    return bool(pending)


@router.get("/notifications", response_model=list[NotificationChecklistItemRead])
def read_checklist_notifications(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[NotificationChecklistItemRead]:
    accessible_trips = session.exec(
        select(Trip.id, Trip.name)
        .join(TripMember, isouter=True)
        .where(
            (Trip.user == current_user)
            | ((TripMember.user == current_user) & (TripMember.joined_at.is_not(None))),
            Trip.archived.is_not(True),
        )
        .distinct()
    ).all()
    trip_names = {trip_id: name for trip_id, name in accessible_trips}
    if not trip_names:
        return []

    items = session.exec(
        select(TripChecklistItem)
        .where(TripChecklistItem.trip_id.in_(trip_names.keys()))
        .where(TripChecklistItem.notify_dt.is_not(None))
        .where(TripChecklistItem.checked.is_not(True))
    ).all()

    entries = session.exec(
        select(TripChecklistEntry, TripChecklist)
        .join(TripChecklist, TripChecklist.id == TripChecklistEntry.checklist_id)
        .where(TripChecklist.trip_id.in_(trip_names.keys()))
        .where(TripChecklistEntry.notify_dt.is_not(None))
        .where(TripChecklistEntry.checked.is_not(True))
    ).all()

    notifications = [
        NotificationChecklistItemRead(
            id=item.id,
            text=item.text,
            notify_dt=item.notify_dt,
            trip_id=item.trip_id,
            trip_name=trip_names[item.trip_id],
        )
        for item in items
    ] + [
        NotificationChecklistItemRead(
            id=entry.id,
            text=entry.text,
            notify_dt=entry.notify_dt,
            trip_id=checklist.trip_id,
            trip_name=trip_names[checklist.trip_id],
            list_id=checklist.id,
            list_name=checklist.name,
        )
        for entry, checklist in entries
    ]

    return sorted(notifications, key=lambda n: n.notify_dt)


@router.get("/{trip_id}", response_model=TripRead)
def read_trip(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripRead:
    db_trip = session.exec(
        select(Trip)
        .options(
            selectinload(Trip.days).selectinload(TripDay.items),
            selectinload(Trip.days).selectinload(TripDay.bookings).selectinload(TripBooking.attachments),
            selectinload(Trip.places),
            selectinload(Trip.image),
            selectinload(Trip.memberships),
            selectinload(Trip.planner_settings),
        )
        .outerjoin(TripMember)
        .where(
            Trip.id == trip_id,
            (Trip.user == current_user)
            | ((TripMember.user == current_user) & (TripMember.joined_at.is_not(None))),
        )
    ).first()

    if not db_trip:
        raise HTTPException(status_code=404, detail="Not found")
    return TripRead.serialize(db_trip)


@router.get("/{trip_id}/planner-settings", response_model=TripPlannerSettingsRead)
def read_trip_planner_settings(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPlannerSettingsRead:
    _get_verified_trip(session, trip_id, current_user)
    return TripPlannerSettingsRead.serialize(session.get(TripPlannerSettings, trip_id))


@router.put("/{trip_id}/planner-settings", response_model=TripPlannerSettingsRead)
def update_trip_planner_settings(
    data: TripPlannerSettingsUpdate,
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPlannerSettingsRead:
    trip = _get_verified_trip(session, trip_id, current_user)
    if trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    replacement = TripPlannerSettings.from_update(trip_id, data)
    existing = session.get(TripPlannerSettings, trip_id)
    if existing:
        for field in (
            "requested_days",
            "start_lat",
            "start_lng",
            "end_lat",
            "end_lng",
            "return_to_start",
            "allowed_profiles",
            "objective",
        ):
            setattr(existing, field, getattr(replacement, field))
    else:
        existing = replacement
        session.add(existing)

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise HTTPException(status_code=500, detail="Failed to update")
    return TripPlannerSettingsRead.serialize(existing)


@router.post("", response_model=TripReadBase)
def create_trip(
    trip: TripCreate,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripReadBase:
    new_trip = Trip(name=trip.name, currency=trip.currency, user=current_user)

    filename = None
    if trip.image:
        image_bytes = b64img_decode(trip.image)
        filename, file_size = save_image_to_file(image_bytes, get_settings().TRIP_IMAGE_SIZE)
        if not filename:
            raise HTTPException(status_code=400, detail="Bad request")

        image = Image(filename=filename, file_size=file_size, user=current_user)
        session.add(image)
        session.flush()
        new_trip.image_id = image.id

    try:
        session.add(new_trip)
        session.flush()
        session.add(TripPlannerSettings(trip_id=new_trip.id))
        session.commit()
    except Exception:
        session.rollback()
        if filename:
            remove_image(filename)
        raise HTTPException(status_code=500, detail="Failed to create")
    return TripReadBase.serialize(new_trip)


@router.put("/{trip_id}", response_model=TripRead)
def update_trip(
    session: SessionDep,
    trip_id: int,
    trip: TripUpdate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived and (trip.archived is not False):
        raise HTTPException(status_code=400, detail="Bad request")

    trip_data = trip.model_dump(exclude_unset=True)

    place_ids = trip_data.pop("place_ids", None)
    if place_ids is not None:  # Could be empty [], so 'in'
        allowed_users = _trip_usernames(session, trip_id)
        new_places = []
        for place_id in place_ids:
            db_place = session.get(Place, place_id)
            if not db_place:
                raise HTTPException(status_code=404, detail="Not found")
            if db_place.user not in allowed_users:
                raise HTTPException(status_code=403, detail="Place not accessible by trip members")
            new_places.append(db_place)
        db_trip.places = new_places

        item_place_ids = {
            item.place.id for day in db_trip.days for item in day.items if item.place is not None
        }
        invalid_place_ids = item_place_ids - set(place.id for place in db_trip.places)
        if invalid_place_ids:  # TripItem references a Place that Trip.places misses
            raise HTTPException(status_code=400, detail="Bad Request")

    trip_image = trip_data.pop("image", None)
    filename = None
    if trip_image:
        image_bytes = b64img_decode(trip_image)
        filename, file_size = save_image_to_file(image_bytes, get_settings().TRIP_IMAGE_SIZE)
        if not filename:
            raise HTTPException(status_code=400, detail="Bad request")

        if db_trip.image:
            session.delete(db_trip.image)
            session.flush()

        image = Image(filename=filename, file_size=file_size, user=current_user)
        session.add(image)
        session.flush()
        session.refresh(db_trip)
        db_trip.image_id = image.id

    for key, value in trip_data.items():
        setattr(db_trip, key, value)

    try:
        session.add(db_trip)
        session.commit()
    except Exception:
        session.rollback()
        if filename:
            remove_image(filename)
        raise HTTPException(status_code=500, detail="Failed to update")
    return TripRead.serialize(db_trip)


@router.delete("/{trip_id}")
def delete_trip(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.user != current_user:
        raise HTTPException(status_code=403, detail="Forbidden")
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    for day in db_trip.days:
        for item in day.items:
            for image in item.images:
                session.delete(image)

    if db_trip.image:
        try:
            session.delete(db_trip.image)
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="Roses are red, violets are blue, if you're reading this, I'm sorry for you",
            )

    session.delete(db_trip)
    session.commit()
    return {}


@router.get("/{trip_id}/balance", response_model=dict[str, TripBalanceEntry])
def get_trip_balance(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    _get_verified_trip(session, trip_id, current_user)
    members = _trip_usernames(session, trip_id)
    if len(members) < 2:
        raise HTTPException(status_code=404, detail="Not found")

    trip_items = session.exec(
        select(TripItem.price, TripItem.paid_by)
        .join(TripDay)
        .where(
            TripDay.trip_id == trip_id,
            TripItem.price.is_not(None),
            TripItem.paid_by.is_not(None),
        )
    ).all()

    paid_by_map = {m: 0 for m in members}
    for item in trip_items:
        if not item.price or not item.paid_by:
            continue
        paid_by_map[item.paid_by] = paid_by_map.get(item.paid_by, 0) + item.price
    xpected_per_person = sum(paid_by_map.values()) / len(members)

    return {
        member: TripBalanceEntry(
            balance=round(paid_by_map[member] - xpected_per_person, 2),
            paid=round(paid_by_map[member], 2),
        )
        for member in paid_by_map
    }


@router.post("/{trip_id}/optimize-day/{day_id}", response_model=DayOptimizationResult)
async def preview_optimize_day(
    data: DayOptimizationPreviewRequest,
    trip_id: int,
    day_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> DayOptimizationResult:
    """Calculate one accessible day's order without changing its persisted sequence."""

    _get_verified_trip(session, trip_id, current_user)
    _get_trip_day_or_404(session, trip_id, day_id)
    items = _ordered_day_items(session, day_id)
    return await _day_optimizer_for_user(session, current_user).optimize_day(
        DayItemSnapshot.from_trip_items(items), data.profile
    )


@router.post("/{trip_id}/optimize", response_model=TripOptimizationPreviewResult)
async def preview_optimize_trip(
    trip_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripOptimizationPreviewResult:
    """Preview a saved-settings whole-trip allocation without persisting it."""

    _get_verified_trip(session, trip_id, current_user)
    preview, _ = await _whole_trip_preview(session, trip_id, current_user)
    return preview


@router.post("/{trip_id}/optimize/apply", response_model=TripOptimizationApplyResult)
async def apply_optimize_trip(
    data: TripOptimizationApplyRequest,
    trip_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripOptimizationApplyResult:
    """Recalculate and atomically persist an explicitly previewed whole-trip plan."""

    trip = _get_verified_trip(session, trip_id, current_user)
    if trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    preview, items = await _whole_trip_preview(session, trip_id, current_user)
    if preview.starting_assignments != data.starting_assignments or preview.snapshot_token != data.snapshot_token:
        raise HTTPException(status_code=409, detail="Trip itinerary or planner settings changed; preview it again before applying")
    if preview.totals is None:
        raise HTTPException(status_code=422, detail="Trip cannot be optimized with the current routing matrix")

    planned_item_ids = tuple(
        item_id
        for day in preview.allocation.days
        for item_id in day.optimization.optimized_item_ids
    )
    expected_item_ids = tuple(item.id for item in items)
    if len(planned_item_ids) != len(expected_item_ids) or set(planned_item_ids) != set(expected_item_ids):
        raise HTTPException(status_code=422, detail="Invalid trip optimization result")

    target_day_ids = list(preview.target_day_ids)
    applied_day_ids: list[int] = []
    eligible_item_ids = set(expected_item_ids)
    try:
        for allocation_day in preview.allocation.days:
            optimized_item_ids = allocation_day.optimization.optimized_item_ids
            if not optimized_item_ids:
                continue
            target_day_id = target_day_ids[allocation_day.day_index]
            if target_day_id is None:
                new_day = TripDay(label=f"Planned day {allocation_day.day_index + 1}", trip_id=trip_id)
                session.add(new_day)
                session.flush()
                target_day_id = new_day.id
                target_day_ids[allocation_day.day_index] = target_day_id

            preserved_sequence = session.exec(
                select(TripItem.sequence)
                .where(TripItem.day_id == target_day_id, TripItem.id.not_in(eligible_item_ids))
                .order_by(TripItem.sequence.desc())
                .limit(1)
            ).first()
            sequence_start = preserved_sequence + 1 if preserved_sequence is not None else 0
            for offset, item_id in enumerate(optimized_item_ids):
                session.exec(
                    update(TripItem)
                    .where(TripItem.id == item_id)
                    .values(day_id=target_day_id, sequence=sequence_start + offset)
                )
            applied_day_ids.append(target_day_id)
        session.commit()
    except Exception:
        session.rollback()
        raise HTTPException(status_code=500, detail="Failed to apply trip optimization")

    return TripOptimizationApplyResult(
        **{
            **preview.model_dump(),
            "target_day_ids": tuple(target_day_ids),
            "applied_day_ids": tuple(applied_day_ids),
        }
    )


@router.post(
    "/{trip_id}/optimize-day/{day_id}/apply",
    response_model=DayOptimizationApplyResult,
)
async def apply_optimize_day(
    data: DayOptimizationApplyRequest,
    trip_id: int,
    day_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> DayOptimizationApplyResult:
    """Recalculate and atomically persist one previously previewed day order."""

    trip = _get_verified_trip(session, trip_id, current_user)
    if trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")
    _get_trip_day_or_404(session, trip_id, day_id)
    items = _ordered_day_items(session, day_id)
    starting_item_ids = tuple(item.id for item in items)
    if starting_item_ids != data.starting_item_ids:
        raise HTTPException(status_code=409, detail="Day itinerary changed; preview it again before applying")

    result = await _day_optimizer_for_user(session, current_user).optimize_day(
        DayItemSnapshot.from_trip_items(items), data.profile
    )
    if result.cost_comparison is None:
        raise HTTPException(status_code=422, detail="Day cannot be optimized with the current routing matrix")
    if result.starting_item_ids != starting_item_ids or set(result.optimized_item_ids) != set(starting_item_ids):
        raise HTTPException(status_code=422, detail="Invalid optimization result")

    try:
        for sequence, item_id in enumerate(result.optimized_item_ids):
            session.exec(
                update(TripItem)
                .where(TripItem.id == item_id, TripItem.day_id == day_id)
                .values(sequence=sequence)
            )
        session.commit()
    except Exception:
        session.rollback()
        raise HTTPException(status_code=500, detail="Failed to apply day optimization")

    return DayOptimizationApplyResult(**result.model_dump())


@router.post("/{trip_id}/days/{day_id}/reorder", response_model=TripDayRead)
def reorder_day_items(
    data: DayManualReorderRequest,
    trip_id: int,
    day_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripDayRead:
    """Atomically persist a complete manual order for one accessible day only."""

    trip = _get_verified_trip(session, trip_id, current_user)
    if trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")
    day = _get_trip_day_or_404(session, trip_id, day_id)
    current_item_ids = tuple(item.id for item in _ordered_day_items(session, day_id))
    if len(data.item_ids) != len(current_item_ids) or set(data.item_ids) != set(current_item_ids):
        raise HTTPException(status_code=422, detail="item_ids must contain every item in this day exactly once")

    try:
        for sequence, item_id in enumerate(data.item_ids):
            session.exec(
                update(TripItem)
                .where(TripItem.id == item_id, TripItem.day_id == day_id)
                .values(sequence=sequence)
            )
        session.commit()
        session.refresh(day)
    except Exception:
        session.rollback()
        raise HTTPException(status_code=500, detail="Failed to reorder day")

    return TripDayRead.serialize(day)


@router.post("/{trip_id}/days", response_model=TripDayRead)
def create_tripday(
    td: TripDayBase,
    trip_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripDayRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)

    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    new_day = TripDay(label=td.label, dt=td.dt, trip_id=trip_id)

    session.add(new_day)
    session.commit()
    session.refresh(new_day)
    return TripDayRead.serialize(new_day)


@router.put("/{trip_id}/days/{day_id}", response_model=TripDayRead)
def update_tripday(
    td: TripDayBase,
    trip_id: int,
    day_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripDayRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)

    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_day = session.get(TripDay, day_id)
    if not db_day or (db_day.trip_id != trip_id):
        raise HTTPException(status_code=400, detail="Bad request")

    td_data = td.model_dump(exclude_unset=True)
    for key, value in td_data.items():
        setattr(db_day, key, value)

    session.add(db_day)
    session.commit()
    session.refresh(db_day)
    return TripDayRead.serialize(db_day)


@router.delete("/{trip_id}/days/{day_id}")
def delete_tripday(
    trip_id: int,
    day_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)

    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_day = session.get(TripDay, day_id)
    if not db_day or (db_day.trip_id != trip_id):
        raise HTTPException(status_code=400, detail="Bad request")

    for item in db_day.items:
        for image in item.images:
            session.delete(image)

    session.delete(db_day)
    session.commit()
    return {}


def _resolve_item_images(
    session: SessionDep,
    images: list[ItemImageInput],
    current_user: str,
    allowed_image_ids: set[int],
) -> tuple[list[Image], list[str]]:
    resolved: list[Image] = []
    new_filenames: list[str] = []
    for entry in images:
        if entry.id is not None:
            image = session.get(Image, entry.id) if entry.id in allowed_image_ids else None
            if not image:
                raise HTTPException(status_code=400, detail="Image not found")
            resolved.append(image)
        elif entry.data:
            image_bytes = b64img_decode(entry.data)
            filename, file_size = save_image_to_file(image_bytes, 0)
            if not filename:
                raise HTTPException(status_code=400, detail="Bad request")
            new_filenames.append(filename)
            image = Image(filename=filename, file_size=file_size, user=current_user)
            session.add(image)
            session.flush()
            resolved.append(image)
    return resolved, new_filenames


def _cover_image_id(images: list[Image], cover_index: int | None) -> int | None:
    if not images:
        return None
    idx = cover_index if cover_index is not None and 0 <= cover_index < len(images) else 0
    return images[idx].id


@router.post("/{trip_id}/days/{day_id}/items", response_model=TripItemRead)
async def create_tripitem(
    item: TripItemCreate,
    trip_id: int,
    day_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripItemRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)

    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_day = session.get(TripDay, day_id)
    if not db_day or (db_day.trip_id != trip_id):
        raise HTTPException(status_code=400, detail="Bad request")

    db_user = session.get(User, current_user)
    links = await resolve_links(None, item.links, db_user.fetch_link_titles)

    new_item = TripItem(
        time=item.time,
        text=item.text,
        comment=item.comment,
        lat=item.lat,
        lng=item.lng,
        day_id=day_id,
        price=item.price,
        status=item.status,
        gpx=item.gpx,
        links=links,
        sequence=_next_trip_item_sequence(session, day_id),
    )

    if item.place is not None:
        place_in_trip = any(place.id == item.place for place in db_trip.places)
        if not place_in_trip:
            raise HTTPException(status_code=400, detail="Bad request")
        new_item.place_id = item.place

    if item.paid_by:
        if db_trip.user != item.paid_by:
            is_member = item.paid_by in _trip_usernames(session, trip_id)
            if not is_member:
                raise HTTPException(status_code=400, detail="User is not a trip member")

        new_item.paid_by = item.paid_by

    if item.attachment_ids:
        attachments = session.exec(
            select(TripAttachment)
            .where(TripAttachment.id.in_(item.attachment_ids))
            .where(TripAttachment.trip_id == trip_id)
        ).all()

        if len(attachments) != len(item.attachment_ids):
            raise HTTPException(status_code=400, detail="One or more attachments not found in trip")

        new_item.attachments = list(attachments)

    new_filenames: list[str] = []
    if item.images:
        # A new item has no existing gallery, so only freshly uploaded (data) entries
        # are valid here - reused ids have nothing to reference yet.
        resolved, new_filenames = _resolve_item_images(session, item.images, current_user, set())
        new_item.images = resolved
        new_item.image_id = _cover_image_id(resolved, item.cover_index)

    try:
        session.add(new_item)
        session.commit()
    except Exception:
        session.rollback()
        for fn in new_filenames:
            remove_image(fn)
        raise HTTPException(status_code=500, detail="Failed to create")
    return TripItemRead.serialize(new_item)


@router.put("/{trip_id}/days/{day_id}/items/{item_id}", response_model=TripItemRead)
async def update_tripitem(
    item: TripItemUpdate,
    trip_id: int,
    day_id: int,
    item_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripItemRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)

    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_day = session.get(TripDay, day_id)
    if not db_day or (db_day.trip_id != trip_id):
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = session.get(TripItem, item_id)
    if not db_item or (db_item.day_id != day_id):
        raise HTTPException(status_code=400, detail="Bad request")

    item_data = item.model_dump(exclude_unset=True)
    if "text" in item_data and not item_data["text"]:
        raise HTTPException(status_code=400, detail="Bad request")

    if "links" in item_data:
        db_user = session.get(User, current_user)
        item_data["links"] = await resolve_links(
            db_item.links, item_data["links"], db_user.fetch_link_titles
        )

    if "place" in item_data:
        place_id = item_data.pop("place")
        db_item.place_id = place_id
        if place_id is not None:
            place_in_trip = any(p.id == place_id for p in db_trip.places)
            if not place_in_trip:
                raise HTTPException(status_code=400, detail="Bad request")

    if "day_id" in item_data:
        new_day_id = item_data.pop("day_id")
        if new_day_id is None:
            raise HTTPException(status_code=400, detail="Bad request")
        new_day = session.get(TripDay, new_day_id)
        if not new_day or new_day.trip_id != trip_id:
            raise HTTPException(status_code=400, detail="Bad request")
        if new_day_id != db_item.day_id:
            db_item.sequence = _next_trip_item_sequence(session, new_day_id)
            db_item.day_id = new_day_id

    if "paid_by" in item_data:
        paid_by = item_data.pop("paid_by")
        if paid_by:
            if paid_by != db_trip.user:
                is_member = item.paid_by in _trip_usernames(session, trip_id)
                if not is_member:
                    raise HTTPException(status_code=400, detail="User is not a trip member")
            db_item.paid_by = paid_by
        else:
            db_item.paid_by = None

    attachment_ids = item_data.pop("attachment_ids", None)
    if attachment_ids is not None:  # Could be empty [], so 'in'
        if attachment_ids:
            attachments = session.exec(
                select(TripAttachment)
                .where(TripAttachment.id.in_(attachment_ids))
                .where(TripAttachment.trip_id == trip_id)
            ).all()

            if len(attachments) != len(attachment_ids):
                raise HTTPException(status_code=400, detail="One or more attachments not found in trip")

            db_item.attachments = list(attachments)
        else:
            db_item.attachments = []

    item_data.pop("cover_index", None)
    # An absent "images" key leaves the gallery untouched; an empty list clears it.
    new_filenames: list[str] = []
    if "images" in item_data:
        item_data.pop("images")
        old_images = list(db_item.images)
        allowed_ids = {img.id for img in old_images}
        resolved, new_filenames = _resolve_item_images(session, item.images or [], current_user, allowed_ids)
        resolved_ids = {img.id for img in resolved}

        db_item.images = resolved
        db_item.image_id = _cover_image_id(resolved, item.cover_index)
        session.flush()

        for old in old_images:
            if old.id not in resolved_ids:
                session.delete(old)

    for key, value in item_data.items():
        setattr(db_item, key, value)

    try:
        session.add(db_item)
        session.commit()
    except Exception:
        session.rollback()
        for fn in new_filenames:
            remove_image(fn)
        raise HTTPException(status_code=500, detail="Failed to update")
    return TripItemRead.serialize(db_item)


@router.delete("/{trip_id}/days/{day_id}/items/{item_id}")
def delete_tripitem(
    trip_id: int,
    day_id: int,
    item_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_day = session.get(TripDay, day_id)
    if not db_day or (db_day.trip_id != trip_id):
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = session.get(TripItem, item_id)
    if not db_item or (db_item.day_id != day_id):
        raise HTTPException(status_code=400, detail="Bad request")

    if db_item.images:
        try:
            for image in db_item.images:
                session.delete(image)
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="Roses are red, violets are blue, if you're reading this, I'm sorry for you",
            )

    session.delete(db_item)
    session.commit()
    return {}


@router.get("/{trip_id}/attachments/{attachment_id}/download")
async def download_trip_attachment(
    session: SessionDep,
    trip_id: int,
    attachment_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    _get_verified_trip(session, trip_id, current_user)
    attachment = session.get(TripAttachment, attachment_id)
    if not attachment or attachment.trip_id != trip_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = attachments_trip_folder_path(trip_id) / attachment.stored_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment not found")

    return FileResponse(path=file_path, filename=attachment.filename, media_type="application/pdf")


@router.get("/{trip_id}/attachments/download-all")
async def download_all_trip_attachments(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    _get_verified_trip(session, trip_id, current_user)
    attachments = session.exec(select(TripAttachment).where(TripAttachment.trip_id == trip_id)).all()
    if not attachments:
        raise HTTPException(status_code=404, detail="No attachments to download")

    buf = BytesIO()
    zip_trip_attachments(trip_id, attachments, buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=attachments.zip"},
    )


@router.get("/{trip_id}/share", response_model=TripShareDetails)
def get_shared_trip_details(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripShareDetails:
    _get_verified_trip(session, trip_id, current_user)
    share = session.exec(select(TripShare).where(TripShare.trip_id == trip_id)).first()
    if not share:
        raise HTTPException(status_code=404, detail="Not found")

    return {"url": f"/s/t/{share.token}", "is_full_access": share.is_full_access}


@router.post("/{trip_id}/share", response_model=TripShareDetails)
def create_shared_trip(
    session: SessionDep,
    trip_id: int,
    data: TripShareCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripShareDetails:
    _get_verified_trip(session, trip_id, current_user)
    shared = session.exec(select(TripShare).where(TripShare.trip_id == trip_id)).first()
    if shared:
        raise HTTPException(status_code=409, detail="The resource already exists")

    token = generate_urlsafe()
    if data.is_full_access:
        token = f"{token[:-3]}ful"
    share = TripShare(token=token, trip_id=trip_id, is_full_access=data.is_full_access)
    session.add(share)
    session.commit()
    return {"url": f"/s/t/{token}", "is_full_access": share.is_full_access}


@router.delete("/{trip_id}/share")
def delete_shared_trip(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    _get_verified_trip(session, trip_id, current_user)
    db_share = session.exec(select(TripShare).where(TripShare.trip_id == trip_id)).first()
    if not db_share:
        raise HTTPException(status_code=404, detail="Not found")

    session.delete(db_share)
    session.commit()
    return {}


def _calendar_url(token: str) -> str:
    return f"/api/trips/calendar/{token}.ics"


@router.get("/{trip_id}/ics")
def download_trip_ics(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    _get_verified_trip(session, trip_id, current_user)
    return _ics_download(_trip_for_ics(session, Trip.id == trip_id))


@router.get("/{trip_id}/calendar", response_model=TripCalendarDetails)
def get_trip_calendar(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripCalendarDetails:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if not db_trip.ics_token:
        raise HTTPException(status_code=404, detail="Not found")
    return {"url": _calendar_url(db_trip.ics_token)}


@router.post("/{trip_id}/calendar", response_model=TripCalendarDetails)
def create_trip_calendar(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripCalendarDetails:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.ics_token:
        raise HTTPException(status_code=409, detail="The resource already exists")

    db_trip.ics_token = generate_urlsafe()
    session.add(db_trip)
    session.commit()
    return {"url": _calendar_url(db_trip.ics_token)}


@router.delete("/{trip_id}/calendar")
def delete_trip_calendar(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if not db_trip.ics_token:
        raise HTTPException(status_code=404, detail="Not found")

    db_trip.ics_token = None
    session.add(db_trip)
    session.commit()
    return {}


@router.get("/{trip_id}/packing", response_model=list[TripPackingListItemRead])
def read_packing_list(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[TripPackingListItemRead]:
    _get_verified_trip(session, trip_id, current_user)
    p_items = session.exec(select(TripPackingListItem).where(TripPackingListItem.trip_id == trip_id))

    return [TripPackingListItemRead.serialize(i) for i in p_items]


@router.post("/{trip_id}/packing", response_model=TripPackingListItemRead)
def create_packing_item(
    session: SessionDep,
    trip_id: int,
    data: TripPackingListItemCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPackingListItemRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    item = TripPackingListItem(**data.model_dump(), trip_id=trip_id)
    session.add(item)
    session.commit()
    session.refresh(item)
    return TripPackingListItemRead.serialize(item)


@router.put("/{trip_id}/packing/{p_id}", response_model=TripPackingListItemRead)
def update_packing_item(
    session: SessionDep,
    p_item: TripPackingListItemUpdate,
    trip_id: int,
    p_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPackingListItemRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = session.exec(
        select(TripPackingListItem).where(
            TripPackingListItem.id == p_id, TripPackingListItem.trip_id == trip_id
        )
    ).one_or_none()

    if not db_item:
        raise HTTPException(status_code=404, detail="Not found")

    item_data = p_item.model_dump(exclude_unset=True)
    for key, value in item_data.items():
        setattr(db_item, key, value)

    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return TripPackingListItemRead.serialize(db_item)


@router.delete("/{trip_id}/packing/{p_id}")
def delete_packing_item(
    session: SessionDep,
    trip_id: int,
    p_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    item = session.exec(
        select(TripPackingListItem).where(
            TripPackingListItem.id == p_id, TripPackingListItem.trip_id == trip_id
        )
    ).one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Not found")

    session.delete(item)
    session.commit()
    return {}


@router.get("/{trip_id}/checklist", response_model=list[TripChecklistItemRead])
def read_checklist(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[TripChecklistItemRead]:
    _get_verified_trip(session, trip_id, current_user)
    items = session.exec(select(TripChecklistItem).where(TripChecklistItem.trip_id == trip_id))
    return [TripChecklistItemRead.serialize(i) for i in items]


@router.post("/{trip_id}/checklist", response_model=TripChecklistItemRead)
def create_checklist_item(
    session: SessionDep,
    trip_id: int,
    data: TripChecklistItemCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripChecklistItemRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    item = TripChecklistItem(**data.model_dump(), trip_id=trip_id)
    session.add(item)
    session.commit()
    session.refresh(item)
    return TripChecklistItemRead.serialize(item)


@router.put("/{trip_id}/checklist/{id}", response_model=TripChecklistItemRead)
def update_checklist_item(
    session: SessionDep,
    item: TripChecklistItemUpdate,
    trip_id: int,
    id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripChecklistItemRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = session.exec(
        select(TripChecklistItem).where(TripChecklistItem.id == id, TripChecklistItem.trip_id == trip_id)
    ).one_or_none()

    if not db_item:
        raise HTTPException(status_code=404, detail="Not found")

    item_data = item.model_dump(exclude_unset=True)
    for key, value in item_data.items():
        setattr(db_item, key, value)

    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return TripChecklistItemRead.serialize(db_item)


@router.delete("/{trip_id}/checklist/{id}")
def delete_checklist_item(
    session: SessionDep,
    trip_id: int,
    id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    item = session.exec(
        select(TripChecklistItem).where(
            TripChecklistItem.id == id,
            TripChecklistItem.trip_id == trip_id,
        )
    ).one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Not found")

    session.delete(item)
    session.commit()
    return {}


@router.get("/{trip_id}/packing-lists", response_model=list[TripPackingListRead])
def read_packing_lists(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[TripPackingListRead]:
    _get_verified_trip(session, trip_id, current_user)
    lists = session.exec(
        select(TripPackingList)
        .where(TripPackingList.trip_id == trip_id)
        .options(selectinload(TripPackingList.items))
    )
    return [TripPackingListRead.serialize(pl) for pl in lists]


@router.post("/{trip_id}/packing-lists", response_model=TripPackingListRead)
def create_packing_list(
    session: SessionDep,
    trip_id: int,
    data: TripPackingListCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPackingListRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    packing_list = TripPackingList(**data.model_dump(), trip_id=trip_id)
    session.add(packing_list)
    session.commit()
    session.refresh(packing_list)
    return TripPackingListRead.serialize(packing_list)


@router.put("/{trip_id}/packing-lists/{list_id}", response_model=TripPackingListRead)
def update_packing_list(
    session: SessionDep,
    data: TripPackingListUpdate,
    trip_id: int,
    list_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPackingListRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_list = _get_own_list_or_404(session, TripPackingList, list_id, trip_id)

    db_list.name = data.name
    session.add(db_list)
    session.commit()
    session.refresh(db_list)
    return TripPackingListRead.serialize(db_list)


@router.delete("/{trip_id}/packing-lists/{list_id}")
def delete_packing_list(
    session: SessionDep,
    trip_id: int,
    list_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_list = _get_own_list_or_404(session, TripPackingList, list_id, trip_id)

    session.delete(db_list)
    session.commit()
    return {}


@router.post("/{trip_id}/packing-lists/{list_id}/items", response_model=TripPackingListEntryRead)
def create_packing_list_entry(
    session: SessionDep,
    trip_id: int,
    list_id: int,
    data: TripPackingListEntryCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPackingListEntryRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    _get_own_list_or_404(session, TripPackingList, list_id, trip_id)

    item = TripPackingListEntry(**data.model_dump(), packing_list_id=list_id)
    session.add(item)
    session.commit()
    session.refresh(item)
    return TripPackingListEntryRead.serialize(item)


@router.put(
    "/{trip_id}/packing-lists/{list_id}/items/{item_id}",
    response_model=TripPackingListEntryRead,
)
def update_packing_list_entry(
    session: SessionDep,
    data: TripPackingListEntryUpdate,
    trip_id: int,
    list_id: int,
    item_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripPackingListEntryRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = _get_own_entry_or_404(
        session,
        TripPackingListEntry,
        TripPackingList,
        TripPackingListEntry.packing_list_id,
        item_id,
        list_id,
        trip_id,
    )

    item_data = data.model_dump(exclude_unset=True)
    for key, value in item_data.items():
        setattr(db_item, key, value)

    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return TripPackingListEntryRead.serialize(db_item)


@router.delete("/{trip_id}/packing-lists/{list_id}/items/{item_id}")
def delete_packing_list_entry(
    session: SessionDep,
    trip_id: int,
    list_id: int,
    item_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = _get_own_entry_or_404(
        session,
        TripPackingListEntry,
        TripPackingList,
        TripPackingListEntry.packing_list_id,
        item_id,
        list_id,
        trip_id,
    )

    session.delete(db_item)
    session.commit()
    return {}


@router.get("/{trip_id}/checklists", response_model=list[TripChecklistRead])
def read_checklists(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[TripChecklistRead]:
    _get_verified_trip(session, trip_id, current_user)
    checklists = session.exec(
        select(TripChecklist)
        .where(TripChecklist.trip_id == trip_id)
        .options(selectinload(TripChecklist.items))
    )
    return [TripChecklistRead.serialize(cl) for cl in checklists]


@router.post("/{trip_id}/checklists", response_model=TripChecklistRead)
def create_checklist(
    session: SessionDep,
    trip_id: int,
    data: TripChecklistCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripChecklistRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    checklist = TripChecklist(**data.model_dump(), trip_id=trip_id)
    session.add(checklist)
    session.commit()
    session.refresh(checklist)
    return TripChecklistRead.serialize(checklist)


@router.put("/{trip_id}/checklists/{list_id}", response_model=TripChecklistRead)
def update_checklist(
    session: SessionDep,
    data: TripChecklistUpdate,
    trip_id: int,
    list_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripChecklistRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_checklist = _get_own_list_or_404(session, TripChecklist, list_id, trip_id)

    db_checklist.name = data.name
    session.add(db_checklist)
    session.commit()
    session.refresh(db_checklist)
    return TripChecklistRead.serialize(db_checklist)


@router.delete("/{trip_id}/checklists/{list_id}")
def delete_checklist(
    session: SessionDep,
    trip_id: int,
    list_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_checklist = _get_own_list_or_404(session, TripChecklist, list_id, trip_id)

    session.delete(db_checklist)
    session.commit()
    return {}


@router.post("/{trip_id}/checklists/{list_id}/items", response_model=TripChecklistEntryRead)
def create_checklist_entry(
    session: SessionDep,
    trip_id: int,
    list_id: int,
    data: TripChecklistEntryCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripChecklistEntryRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    _get_own_list_or_404(session, TripChecklist, list_id, trip_id)

    item = TripChecklistEntry(**data.model_dump(), checklist_id=list_id)
    session.add(item)
    session.commit()
    session.refresh(item)
    return TripChecklistEntryRead.serialize(item)


@router.put(
    "/{trip_id}/checklists/{list_id}/items/{item_id}",
    response_model=TripChecklistEntryRead,
)
def update_checklist_entry(
    session: SessionDep,
    data: TripChecklistEntryUpdate,
    trip_id: int,
    list_id: int,
    item_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripChecklistEntryRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = _get_own_entry_or_404(
        session,
        TripChecklistEntry,
        TripChecklist,
        TripChecklistEntry.checklist_id,
        item_id,
        list_id,
        trip_id,
    )

    item_data = data.model_dump(exclude_unset=True)
    for key, value in item_data.items():
        setattr(db_item, key, value)

    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return TripChecklistEntryRead.serialize(db_item)


@router.delete("/{trip_id}/checklists/{list_id}/items/{item_id}")
def delete_checklist_entry(
    session: SessionDep,
    trip_id: int,
    list_id: int,
    item_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_item = _get_own_entry_or_404(
        session,
        TripChecklistEntry,
        TripChecklist,
        TripChecklistEntry.checklist_id,
        item_id,
        list_id,
        trip_id,
    )

    session.delete(db_item)
    session.commit()
    return {}


@router.get("/{trip_id}/members", response_model=list[TripMemberRead])
def read_trip_members(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[TripMemberRead]:
    _get_verified_trip(session, trip_id, current_user)
    members: list[TripMemberRead] = []
    owner = session.exec(select(Trip.user).where(Trip.id == trip_id)).first()
    members.append(TripMemberRead(user=owner, invited_by=None, invited_at=None, joined_at=None))

    db_members = session.exec(select(TripMember).where(TripMember.trip_id == trip_id)).all()
    members.extend(TripMemberRead.serialize(m) for m in db_members)
    return members


@router.post("/{trip_id}/members", response_model=TripMemberRead)
def invite_trip_member(
    session: SessionDep,
    trip_id: int,
    data: TripMemberCreate,
    current_user: Annotated[str, Depends(get_current_username)],
) -> TripMemberRead:
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    if db_trip.user == data.user:
        raise HTTPException(status_code=409, detail="The resource already exists")

    exists = session.exec(
        select(TripMember.id)
        .where(
            TripMember.trip_id == trip_id,
            TripMember.user == data.user,
        )
        .limit(1)
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="The resource already exists")

    db_user = session.get(User, data.user)
    if not db_user:
        raise HTTPException(status_code=404, detail="Not found")

    new_member = TripMember(trip_id=trip_id, user=data.user, invited_by=current_user)
    session.add(new_member)
    session.commit()
    session.refresh(new_member)
    return TripMemberRead.serialize(new_member)


@router.delete("/{trip_id}/members/{username}")
def delete_trip_member(
    session: SessionDep,
    trip_id: int,
    username: str,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    if current_user == db_trip.user and current_user == username:
        raise HTTPException(status_code=400, detail="Bad request")

    if current_user != db_trip.user and current_user != username:
        raise HTTPException(status_code=403, detail="Forbidden")

    member = session.exec(
        select(TripMember).where(
            TripMember.user == username,
            TripMember.trip_id == trip_id,
        )
    ).one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Not found")

    # Set NULL to TripItem.paid_by for this username
    trip_item_ids = session.exec(
        select(TripItem.id).join(TripDay).where(TripDay.trip_id == trip_id, TripItem.paid_by == username)
    ).all()

    if trip_item_ids:
        session.exec(update(TripItem).where(TripItem.id.in_(trip_item_ids)).values(paid_by=None))

    session.delete(member)
    session.commit()
    return {}


@router.post("/{trip_id}/members/accept")
def accept_invite(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_member = session.exec(
        select(TripMember).where(TripMember.trip_id == trip_id, TripMember.user == current_user)
    ).one_or_none()
    if not db_member:
        raise HTTPException(status_code=404, detail="Not found")
    if db_member.joined_at:
        raise HTTPException(status_code=409, detail="Already a member")
    db_member.joined_at = dt_utc()
    session.add(db_member)
    session.commit()
    return {}


@router.post("/{trip_id}/members/decline")
def decline_invite(
    session: SessionDep,
    trip_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_member = session.exec(
        select(TripMember).where(TripMember.trip_id == trip_id, TripMember.user == current_user)
    ).one_or_none()
    if not db_member:
        raise HTTPException(status_code=404, detail="Not found")
    if db_member.joined_at:
        raise HTTPException(status_code=409, detail="Already a member")
    session.delete(db_member)
    session.commit()
    return {}


@router.post("/{trip_id}/attachments", response_model=TripAttachmentRead)
def create_trip_attachment(
    trip_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
    file: UploadFile = File(...),
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    db_attachment = TripAttachment(
        filename=file.filename,
        content_type=file.content_type,
        file_size=file.size,
        uploaded_by=current_user,
        trip_id=trip_id,
    )
    try:
        stored_filename = save_attachment(trip_id, file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not stored_filename:
        raise HTTPException(status_code=400, detail="Bad request")

    db_attachment.stored_filename = stored_filename
    session.add(db_attachment)
    session.commit()
    session.refresh(db_attachment)
    return TripAttachmentRead.serialize(db_attachment)


@router.delete("/{trip_id}/attachments/{attachment_id}")
async def delete_trip_attachment(
    session: SessionDep,
    trip_id: int,
    attachment_id: int,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_trip = _get_verified_trip(session, trip_id, current_user)
    if db_trip.archived:
        raise HTTPException(status_code=400, detail="Bad request")

    attachment = session.get(TripAttachment, attachment_id)
    if not attachment or attachment.trip_id != trip_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    session.delete(attachment)
    session.commit()
    return {}


@router.get("/shared/{token}", response_model=TripRead | TripShareRead)
def read_shared_trip(
    session: SessionDep,
    token: str,
) -> TripRead | TripShareRead:
    share = _trip_from_token_or_404(session, token)
    db_trip = session.get(Trip, share.trip_id)
    if not db_trip:
        raise HTTPException(status_code=404, detail="Not found")

    return TripRead.serialize(db_trip) if share.is_full_access else TripShareRead.serialize(db_trip)


@router.get("/shared/{token}/ics")
def download_shared_trip_ics(session: SessionDep, token: str):
    share = _trip_from_token_or_404(session, token)
    return _ics_download(_trip_for_ics(session, Trip.id == share.trip_id))


@router.get("/calendar/{token}.ics")
def read_trip_calendar_feed(session: SessionDep, token: str, request: Request):
    # Subscription feed. Calendar clients can't send an Authorization header, so
    # the token in the URL is the whole credential - hence a dedicated,
    # revocable, read-only one rather than the user's api_token.
    trip = _trip_for_ics(session, Trip.ics_token == token)
    body = build_trip_ics(trip)

    # Feeds get polled forever by every subscribed device; DTSTAMP is coarse
    # enough (see utils/ical.py) that the body is byte-stable within a day, so
    # this actually collapses to a 304 instead of resending the whole calendar.
    etag = f'"{md5(body.encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"ETag": etag, "Cache-Control": "private, max-age=3600"},
    )


@router.get("/shared/{token}/packing", response_model=list[TripPackingListItemRead])
def read_shared_trip_packing_list(
    session: SessionDep,
    token: str,
) -> list[TripPackingListItemRead]:
    p_items = session.exec(
        select(TripPackingListItem).where(
            TripPackingListItem.trip_id == _trip_from_token_or_404(session, token).trip_id
        )
    )
    return [TripPackingListItemRead.serialize(i) for i in p_items]


@router.get("/shared/{token}/checklist", response_model=list[TripChecklistItemRead])
def read_shared_trip_checklist(
    session: SessionDep,
    token: str,
) -> list[TripChecklistItemRead]:
    items = session.exec(
        select(TripChecklistItem).where(
            TripChecklistItem.trip_id == _trip_from_token_or_404(session, token).trip_id
        )
    )
    return [TripChecklistItemRead.serialize(i) for i in items]


@router.get("/shared/{token}/packing-lists", response_model=list[TripPackingListRead])
def read_shared_trip_packing_lists(
    session: SessionDep,
    token: str,
) -> list[TripPackingListRead]:
    lists = session.exec(
        select(TripPackingList)
        .where(TripPackingList.trip_id == _trip_from_token_or_404(session, token).trip_id)
        .options(selectinload(TripPackingList.items))
    )
    return [TripPackingListRead.serialize(pl) for pl in lists]


@router.get("/shared/{token}/checklists", response_model=list[TripChecklistRead])
def read_shared_trip_checklists(
    session: SessionDep,
    token: str,
) -> list[TripChecklistRead]:
    checklists = session.exec(
        select(TripChecklist)
        .where(TripChecklist.trip_id == _trip_from_token_or_404(session, token).trip_id)
        .options(selectinload(TripChecklist.items))
    )
    return [TripChecklistRead.serialize(cl) for cl in checklists]


@router.get("/shared/{token}/attachments/{attachment_id}/download")
async def download_shared_trip_attachment(
    session: SessionDep,
    token: str,
    attachment_id: int,
):
    _trip = _trip_from_token_or_404(session, token)
    if not _trip.is_full_access:
        raise HTTPException(status_code=404, detail="Attachment not found")
    attachment = session.exec(
        select(TripAttachment).where(
            TripAttachment.trip_id == _trip.trip_id, TripAttachment.id == attachment_id
        )
    ).first()

    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = attachments_trip_folder_path(_trip.trip_id) / attachment.stored_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment not found")

    return FileResponse(path=file_path, filename=attachment.filename, media_type="application/pdf")


@router.get("/shared/{token}/attachments/download-all")
async def download_all_shared_trip_attachments(
    session: SessionDep,
    token: str,
):
    share = _trip_from_token_or_404(session, token)
    if not share.is_full_access:
        raise HTTPException(status_code=404, detail="Not found")

    db_trip = session.get(Trip, share.trip_id)
    if not db_trip:
        raise HTTPException(status_code=404, detail="Not found")

    attachments = session.exec(select(TripAttachment).where(TripAttachment.trip_id == share.trip_id)).all()
    if not attachments:
        raise HTTPException(status_code=404, detail="No attachments to download")

    buf = BytesIO()
    zip_trip_attachments(db_trip.id, attachments, buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=attachments.zip"},
    )
