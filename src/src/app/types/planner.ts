import { TripDay } from './trip';

export type PlannerRoutingProfile = 'car' | 'foot' | 'bike';

export interface DayRouteCost {
  duration_s: number;
  distance_m: number | null;
}

export interface DayCostComparison {
  starting: DayRouteCost;
  optimized: DayRouteCost;
  duration_saved_s: number;
  distance_saved_m: number | null;
}

export interface RoutingFailure {
  kind: string;
  message: string;
  provider: string;
  profile: PlannerRoutingProfile;
}

export interface DayOptimizationDiagnostic {
  kind: 'coordinateless_item' | 'matrix_failure' | 'incomplete_matrix' | 'matrix_snapshot_mismatch' | 'time_budget_overflow';
  message: string;
  item_ids: number[];
  routing_failure?: RoutingFailure | null;
}

export interface DayScheduleItemEstimate {
  item_id: number;
  arrival_time: string;
  departure_time: string;
  travel_minutes_before: number;
  visit_minutes: number;
}

export interface DayScheduleEstimate {
  start_time: string;
  end_time: string;
  usable_minutes: number;
  travel_minutes: number;
  visit_minutes: number;
  total_minutes: number;
  overflow_minutes: number;
  items: DayScheduleItemEstimate[];
}

export interface DayOptimizationResult {
  starting_item_ids: number[];
  optimized_item_ids: number[];
  cost_comparison: DayCostComparison | null;
  schedule: DayScheduleEstimate | null;
  diagnostics: DayOptimizationDiagnostic[];
}

export interface DayOptimizationApplyResult extends DayOptimizationResult {
  applied: true;
}

export interface DayOptimizationRequest {
  profile: PlannerRoutingProfile;
}

export interface DayOptimizationApplyRequest extends DayOptimizationRequest {
  starting_item_ids: number[];
}

export interface DayManualReorderRequest {
  item_ids: number[];
}

export type ReorderedTripDay = TripDay;

export type TripPlannerObjective = 'duration' | 'distance';

export interface TripPlannerSettings {
  requested_days: number;
  start_location: { lat: number; lng: number } | null;
  end_location: { lat: number; lng: number } | null;
  return_to_start: boolean;
  allowed_profiles: PlannerRoutingProfile[];
  objective: TripPlannerObjective;
}

export interface TripPlanningSnapshotAssignment {
  item_id: number;
  day_id: number;
  sequence: number;
}

export interface TripAllocationDiagnostic {
  kind:
    | 'coordinateless_item'
    | 'matrix_failure'
    | 'incomplete_matrix'
    | 'matrix_snapshot_mismatch'
    | 'distance_objective_unavailable';
  message: string;
  item_ids: number[];
  routing_failure?: RoutingFailure | null;
}

export interface TripAllocationDay {
  day_index: number;
  item_ids: number[];
  optimization: DayOptimizationResult;
}

export interface TripAllocationResult {
  requested_days: number;
  profile: PlannerRoutingProfile;
  days: TripAllocationDay[];
  diagnostics: TripAllocationDiagnostic[];
}

export interface TripPlanningTotals {
  starting_duration_s: number;
  optimized_duration_s: number;
  starting_distance_m: number | null;
  optimized_distance_m: number | null;
}

export interface TripOptimizationPreviewResult {
  starting_assignments: TripPlanningSnapshotAssignment[];
  snapshot_token: string;
  target_day_ids: (number | null)[];
  allocation: TripAllocationResult;
  totals: TripPlanningTotals | null;
}

export interface TripOptimizationApplyRequest {
  starting_assignments: TripPlanningSnapshotAssignment[];
  snapshot_token: string;
}

export interface TripOptimizationApplyResult extends TripOptimizationPreviewResult {
  applied: true;
  applied_day_ids: number[];
}
