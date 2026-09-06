import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  DayManualReorderRequest,
  DayOptimizationApplyRequest,
  DayOptimizationApplyResult,
  DayOptimizationRequest,
  DayOptimizationResult,
  ReorderedTripDay,
} from '../types/planner';

@Injectable({ providedIn: 'root' })
export class DayPlannerService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = '/api';

  preview(tripId: number, dayId: number, request: DayOptimizationRequest): Observable<DayOptimizationResult> {
    return this.http.post<DayOptimizationResult>(`${this.apiBaseUrl}/trips/${tripId}/optimize-day/${dayId}`, request);
  }

  apply(
    tripId: number,
    dayId: number,
    request: DayOptimizationApplyRequest,
  ): Observable<DayOptimizationApplyResult> {
    return this.http.post<DayOptimizationApplyResult>(
      `${this.apiBaseUrl}/trips/${tripId}/optimize-day/${dayId}/apply`,
      request,
    );
  }

  reorder(tripId: number, dayId: number, request: DayManualReorderRequest): Observable<ReorderedTripDay> {
    return this.http.post<ReorderedTripDay>(`${this.apiBaseUrl}/trips/${tripId}/days/${dayId}/reorder`, request);
  }
}
