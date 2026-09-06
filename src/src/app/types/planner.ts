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
  kind: 'coordinateless_item' | 'matrix_failure' | 'incomplete_matrix' | 'matrix_snapshot_mismatch';
  message: string;
  item_ids: number[];
  routing_failure?: RoutingFailure | null;
}

export interface DayOptimizationResult {
  starting_item_ids: number[];
  optimized_item_ids: number[];
  cost_comparison: DayCostComparison | null;
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
