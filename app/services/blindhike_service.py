from datetime import UTC, datetime, timedelta
from math import atan2, cos, radians, sin, sqrt
from typing import Any
from uuid import uuid4

from app.dependencies import DbSession
from app.repositories.blindhike_repository import BlindHikeRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class BlindHikeService(GameLogicService):
    _MARKER_ACTION_NAME = "blindhike.marker.add"
    _DEFAULT_FINISH_RADIUS_METERS = 25.0
    _EARTH_RADIUS_METERS = 6371000.0

    def __init__(self) -> None:
        """Initialize Blind Hike game logic service."""
        super().__init__("blindhike", repository=BlindHikeRepository())

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Best-effort float conversion returning `None` for invalid values."""
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric == numeric else None

    @staticmethod
    def _parse_marker_coordinates(marker_id: str) -> tuple[float, float] | None:
        """Parse marker id strings into latitude/longitude coordinates."""
        raw = str(marker_id or "").strip()
        if raw == "":
            return None

        separators = [",", "|", ":", ";"]
        parts: list[str] = [raw]
        for separator in separators:
            if separator in raw:
                parts = [chunk.strip() for chunk in raw.split(separator)]
                break

        if len(parts) < 2:
            return None

        latitude: float | None = None
        longitude: float | None = None

        for index in range(0, len(parts) - 1):
            lat_candidate = BlindHikeService._safe_float(parts[index])
            lon_candidate = BlindHikeService._safe_float(parts[index + 1])
            if lat_candidate is None or lon_candidate is None:
                continue
            if lat_candidate < -90 or lat_candidate > 90:
                continue
            if lon_candidate < -180 or lon_candidate > 180:
                continue
            latitude = lat_candidate
            longitude = lon_candidate
            break

        if latitude is None or longitude is None:
            return None

        return (latitude, longitude)

    def _extract_team_markers(self, game_state: dict[str, Any], team_id: str) -> list[dict[str, Any]]:
        """Extract team marker trail from game-state claims payload."""
        claims = game_state.get("claims")
        if not isinstance(claims, dict):
            return []

        claim_prefix = f"{self._MARKER_ACTION_NAME}:{team_id}:"
        markers: list[dict[str, Any]] = []

        for claim_key, claim_payload in claims.items():
            key = str(claim_key or "")
            if not key.startswith(claim_prefix):
                continue

            marker_id = key[len(claim_prefix):]
            parsed = self._parse_marker_coordinates(marker_id)
            if parsed is None:
                continue

            placed_at = ""
            if isinstance(claim_payload, dict):
                placed_at = str(claim_payload.get("at") or "")

            markers.append({
                "id": marker_id,
                "lat": parsed[0],
                "lon": parsed[1],
                "placed_at": placed_at,
            })

        markers.sort(key=lambda item: str(item.get("placed_at") or ""))
        return markers

    @staticmethod
    def _extract_team_markers_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert marker rows to normalized marker list sorted by placement time."""
        markers: list[dict[str, Any]] = []
        for row in rows:
            lat = BlindHikeService._safe_float(row.get("latitude"))
            lon = BlindHikeService._safe_float(row.get("longitude"))
            if lat is None or lon is None:
                continue

            placed_at = row.get("placed_at")
            if isinstance(placed_at, datetime):
                placed_at_value = placed_at.isoformat()
            else:
                placed_at_value = str(placed_at or "")

            markers.append({
                "id": str(row.get("id") or ""),
                "lat": lat,
                "lon": lon,
                "placed_at": placed_at_value,
            })

        markers.sort(key=lambda item: str(item.get("placed_at") or ""))
        return markers

    def _build_marker_highscore_from_rows(
        self,
        db: DbSession,
        game_id: str,
        marker_rows: list[dict[str, Any]],
        finished_team_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build highscore rows from persisted marker records."""
        counts_by_team: dict[str, int] = {}
        for row in marker_rows:
            team_id = str(row.get("team_id") or "")
            if not team_id:
                continue
            counts_by_team[team_id] = int(counts_by_team.get(team_id, 0)) + 1

        normalized_finished_team_ids = finished_team_ids or set()

        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        highscore: list[dict[str, Any]] = []
        for team in teams:
            team_id = str(team.get("id") or "")
            highscore.append({
                "team_id": team_id,
                "name": str(team.get("name") or ""),
                "logo_path": str(team.get("logo_path") or ""),
                "markers": int(counts_by_team.get(team_id, 0)),
                "finished": team_id in normalized_finished_team_ids,
            })

        highscore.sort(key=lambda row: (-int(row.get("markers") or 0), str(row.get("name") or "").lower()))
        return highscore

    @staticmethod
    def _distance_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
        """Compute distance between two coordinates in meters via Haversine."""
        lat1 = radians(lat_a)
        lon1 = radians(lon_a)
        lat2 = radians(lat_b)
        lon2 = radians(lon_b)

        dlat = lat2 - lat1
        dlon = lon2 - lon1
        hav = (sin(dlat / 2) ** 2) + (cos(lat1) * cos(lat2) * (sin(dlon / 2) ** 2))
        c = 2 * atan2(sqrt(hav), sqrt(1 - hav))
        return BlindHikeService._EARTH_RADIUS_METERS * c

    def _is_finished_marker(
        self,
        marker_lat: float | None,
        marker_lon: float | None,
        target_lat: float | None,
        target_lon: float | None,
        finish_radius_meters: float,
    ) -> bool:
        """Return whether a marker falls within finish radius of target location."""
        if marker_lat is None or marker_lon is None:
            return False
        if target_lat is None or target_lon is None:
            return False
        if finish_radius_meters <= 0:
            return False

        return self._distance_meters(marker_lat, marker_lon, target_lat, target_lon) <= finish_radius_meters

    def _resolve_finish_radius_meters(self, config: dict[str, Any]) -> float:
        """Resolve configured finish radius with safe fallback default."""
        configured = self._safe_float(config.get("finish_radius_meters"))
        if configured is None or configured <= 0:
            return self._DEFAULT_FINISH_RADIUS_METERS
        return configured

    def _finished_team_ids_from_rows(
        self,
        marker_rows: list[dict[str, Any]],
        *,
        target_lat: float | None,
        target_lon: float | None,
        finish_radius_meters: float,
    ) -> set[str]:
        """Derive teams that have placed at least one finishing marker."""
        finished: set[str] = set()
        if target_lat is None or target_lon is None:
            return finished

        for row in marker_rows:
            team_id = str(row.get("team_id") or "")
            if not team_id or team_id in finished:
                continue
            marker_lat = self._safe_float(row.get("latitude"))
            marker_lon = self._safe_float(row.get("longitude"))
            if self._is_finished_marker(marker_lat, marker_lon, target_lat, target_lon, finish_radius_meters):
                finished.add(team_id)
        return finished

    def _transform_coordinates(
        self,
        latitude: float | None,
        longitude: float | None,
        config: dict[str, Any],
    ) -> tuple[float, float] | None:
        """Apply configured flips, scaling, and rotation to one coordinate pair."""
        if latitude is None or longitude is None:
            return None

        transformed_lat = float(latitude)
        transformed_lon = float(longitude)

        if bool(config.get("vertical_flip")):
            transformed_lat = -transformed_lat
        if bool(config.get("horizontal_flip")):
            transformed_lon = -transformed_lon

        scale_factor = self._safe_float(config.get("scale_factor"))
        if scale_factor is not None and scale_factor > 0:
            transformed_lat *= scale_factor
            transformed_lon *= scale_factor

        rotation = self._safe_float(config.get("rotation"))
        if rotation is not None and rotation != 0:
            theta = radians(rotation)
            cos_theta = cos(theta)
            sin_theta = sin(theta)
            rotated_lat = (transformed_lat * cos_theta) - (transformed_lon * sin_theta)
            rotated_lon = (transformed_lat * sin_theta) + (transformed_lon * cos_theta)
            transformed_lat = rotated_lat
            transformed_lon = rotated_lon

        return (transformed_lat, transformed_lon)

    def _build_marker_highscore(self, db: DbSession, game_id: str, game_state: dict[str, Any]) -> list[dict[str, Any]]:
        """Build marker highscore rows from in-memory game state data."""
        team_state = game_state.get("team_state")
        team_state_by_id = team_state if isinstance(team_state, dict) else {}
        teams = self._repository.fetch_teams_by_game_id(db, game_id)

        highscore: list[dict[str, Any]] = []
        for team in teams:
            team_id = str(team.get("id") or "")
            row = team_state_by_id.get(team_id)
            row_dict = row if isinstance(row, dict) else {}
            highscore.append({
                "team_id": team_id,
                "name": str(team.get("name") or ""),
                "logo_path": str(team.get("logo_path") or ""),
                "markers": int(row_dict.get("actions") or 0),
            })

        highscore.sort(key=lambda row: (-int(row.get("markers") or 0), str(row.get("name") or "").lower()))
        return highscore

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> dict[str, Any]:
        """Build team bootstrap payload including transformed markers and target."""
        config = self._repository.get_configuration(db, game_id)
        marker_rows = self._repository.fetch_markers_by_game_id(db, game_id)
        team_marker_rows = [row for row in marker_rows if str(row.get("team_id") or "") == str(team_id)]
        team = self._repository.get_team_by_game_and_id(db, game_id, team_id)
        geo_score = int((team or {}).get("geo_score") or 0)

        target_lat = self._safe_float(config.get("target_lat"))
        target_lon = self._safe_float(config.get("target_lon"))
        finish_radius_meters = self._resolve_finish_radius_meters(config)
        finished_team_ids = self._finished_team_ids_from_rows(
            marker_rows,
            target_lat=target_lat,
            target_lon=target_lon,
            finish_radius_meters=finish_radius_meters,
        )
        is_finished = str(team_id) in finished_team_ids
        target = None
        transformed_target = self._transform_coordinates(target_lat, target_lon, config)
        if transformed_target is not None:
            target = {
                "lat": transformed_target[0],
                "lon": transformed_target[1],
            }

        raw_team_markers = self._extract_team_markers_from_rows(team_marker_rows)
        team_markers: list[dict[str, Any]] = []
        for marker in raw_team_markers:
            marker_lat = self._safe_float(marker.get("lat"))
            marker_lon = self._safe_float(marker.get("lon"))
            transformed = self._transform_coordinates(marker_lat, marker_lon, config)
            if transformed is None:
                continue
            team_markers.append({
                **marker,
                "lat": transformed[0],
                "lon": transformed[1],
            })

        marker_limit = config.get("max_markers")

        return {
            "version": len(marker_rows),
            "team_id": team_id,
            "score": geo_score,
            "score_delta": 0,
            "actions": len(team_markers),
            "last_action_at": team_markers[-1].get("placed_at") if team_markers else None,
            "last_actions": [],
            "target": target,
            "finished": is_finished,
            "finish_radius_meters": int(finish_radius_meters),
            "marker_limit": int(marker_limit) if marker_limit is not None else None,
            "marker_cooldown": int(config.get("marker_cooldown") or 0),
            "team_markers": team_markers,
            "highscore": self._build_marker_highscore_from_rows(db, game_id, marker_rows, finished_team_ids=finished_team_ids),
        }

    def get_admin_overview(self, db: DbSession, game_id: str) -> dict[str, Any]:
        """Build admin overview including marker map and team progress."""
        config = self._repository.get_configuration(db, game_id)
        marker_rows = self._repository.fetch_markers_by_game_id(db, game_id)

        target_lat = self._safe_float(config.get("target_lat"))
        target_lon = self._safe_float(config.get("target_lon"))
        finish_radius_meters = self._resolve_finish_radius_meters(config)
        finished_team_ids = self._finished_team_ids_from_rows(
            marker_rows,
            target_lat=target_lat,
            target_lon=target_lon,
            finish_radius_meters=finish_radius_meters,
        )
        target = None
        if target_lat is not None and target_lon is not None:
            target = {
                "lat": target_lat,
                "lon": target_lon,
            }

        markers: list[dict[str, Any]] = []
        counts_by_team: dict[str, int] = {}
        for row in marker_rows:
            lat = self._safe_float(row.get("latitude"))
            lon = self._safe_float(row.get("longitude"))
            team_id = str(row.get("team_id") or "")
            if lat is None or lon is None or team_id == "":
                continue

            placed_at = row.get("placed_at")
            placed_value = placed_at.isoformat() if isinstance(placed_at, datetime) else str(placed_at or "")
            markers.append({
                "id": str(row.get("id") or ""),
                "team_id": team_id,
                "lat": lat,
                "lon": lon,
                "placed_at": placed_value,
            })
            counts_by_team[team_id] = int(counts_by_team.get(team_id, 0)) + 1

        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        overview_teams: list[dict[str, Any]] = []
        for team in teams:
            team_id = str(team.get("id") or "")
            overview_teams.append({
                "team_id": team_id,
                "name": str(team.get("name") or ""),
                "markers": int(counts_by_team.get(team_id, 0)),
                "finished": team_id in finished_team_ids,
            })

        overview_teams.sort(key=lambda row: (-int(row.get("markers") or 0), str(row.get("name") or "").lower()))
        markers.sort(key=lambda row: str(row.get("placed_at") or ""))

        return {
            "version": len(markers),
            "target": target,
            "finish_radius_meters": int(finish_radius_meters),
            "teams": overview_teams,
            "markers": markers,
        }

    def add_marker(self, db: DbSession, *, game_id: str, team_id: str, marker_id: str) -> GameActionResult:
        """Validate and store a team marker, enforcing finish/limit/cooldown rules."""
        parsed_marker = self._parse_marker_coordinates(marker_id)
        if parsed_marker is None:
            return GameActionResult(
                success=False,
                message_key="blindhike.validation.invalidMarker",
                action_id="",
                points_awarded=0,
                state_version=0,
            )

        config = self._repository.get_configuration(db, game_id)
        target_lat = self._safe_float(config.get("target_lat"))
        target_lon = self._safe_float(config.get("target_lon"))
        finish_radius_meters = self._resolve_finish_radius_meters(config)

        existing_team_markers = self._repository.fetch_markers_by_team(db, game_id, team_id)
        already_finished = any(
            self._is_finished_marker(
                self._safe_float(marker.get("latitude")),
                self._safe_float(marker.get("longitude")),
                target_lat,
                target_lon,
                finish_radius_meters,
            )
            for marker in existing_team_markers
        )
        if already_finished:
            return GameActionResult(
                success=False,
                message_key="blindhike.marker.finished",
                action_id="",
                points_awarded=0,
                state_version=len(existing_team_markers),
            )

        max_markers = config.get("max_markers")
        if max_markers is not None:
            marker_count = len(existing_team_markers)
            if marker_count >= int(max_markers):
                return GameActionResult(
                    success=False,
                    message_key="blindhike.marker.limitReached",
                    action_id="",
                    points_awarded=0,
                    state_version=marker_count,
                )

        marker_cooldown = int(config.get("marker_cooldown") or 0)
        if marker_cooldown > 0:
            last_marker = self._repository.get_last_marker_for_team(db, game_id, team_id)
            if last_marker is not None:
                last_placed = self._repository.marker_placed_at_datetime(last_marker)
                now = datetime.now(UTC).replace(tzinfo=None)
                if last_placed is not None:
                    if now < (last_placed + timedelta(seconds=marker_cooldown)):
                        return GameActionResult(
                            success=False,
                            message_key="blindhike.marker.cooldownActive",
                            action_id="",
                            points_awarded=0,
                            state_version=len(existing_team_markers),
                        )

        latitude, longitude = parsed_marker
        placed_at = datetime.now(UTC).replace(tzinfo=None)
        marker_row = {
            "id": str(uuid4()),
            "latitude": latitude,
            "longitude": longitude,
            "placed_at": placed_at,
            "game_id": game_id,
            "team_id": team_id,
        }

        self._repository.create_marker_without_commit(db, marker_row)
        self._repository.commit_changes(db)

        marker_count = len(existing_team_markers) + 1
        return GameActionResult(
            success=True,
            message_key="blindhike.marker.added",
            action_id=str(marker_row["id"]),
            points_awarded=0,
            state_version=marker_count,
        )
