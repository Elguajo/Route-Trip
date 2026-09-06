import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  TripOptimizationApplyRequest,
  TripOptimizationApplyResult,
  TripOptimizationPreviewResult,
} from '../types/planner';

@Injectable({ providedIn: 'root' })
export class TripPlannerService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = '/api';

  preview(tripId: number): Observable<TripOptimizationPreviewResult> {
    return this.http.post<TripOptimizationPreviewResult>(`${this.apiBaseUrl}/trips/${tripId}/optimize`, {});
  }

  apply(tripId: number, request: TripOptimizationApplyRequest): Observable<TripOptimizationApplyResult> {
    return this.http.post<TripOptimizationApplyResult>(`${this.apiBaseUrl}/trips/${tripId}/optimize/apply`, request);
  }
}
