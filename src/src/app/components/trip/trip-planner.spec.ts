import { signal } from '@angular/core';
import { of, throwError } from 'rxjs';
import { DayOptimizationResult } from '../../types/planner';
import { Trip, TripDay } from '../../types/trip';
import { TripComponent } from './trip.component';

const day: TripDay = {
  id: 7,
  label: 'Day one',
  items: [
    { id: 1, sequence: 0, day_id: 7, text: 'First', time: '09:00' },
    { id: 2, sequence: 1, day_id: 7, text: 'Second', time: '10:00' },
  ],
};

const trip: Trip = {
  id: 11,
  name: 'Planner test',
  user: 'user',
  days: [day],
  collaborators: [],
  currency: 'USD',
  places: [],
  place_ids: [],
};

const preview: DayOptimizationResult = {
  starting_item_ids: [1, 2],
  optimized_item_ids: [2, 1],
  cost_comparison: {
    starting: { duration_s: 600, distance_m: 1200 },
    optimized: { duration_s: 420, distance_m: 800 },
    duration_saved_s: 180,
    distance_saved_m: 400,
  },
  schedule: null,
  diagnostics: [{ kind: 'coordinateless_item', item_ids: [], message: 'A diagnostic' }],
};

const tripPlanPreview = {
  starting_assignments: [
    { item_id: 1, day_id: 7, sequence: 0 },
    { item_id: 2, day_id: 7, sequence: 1 },
  ],
  snapshot_token: 'preview-token',
  target_day_ids: [7],
  allocation: {
    requested_days: 1,
    profile: 'car' as const,
    days: [{ day_index: 0, item_ids: [1, 2], optimization: preview }],
    diagnostics: [],
  },
  totals: {
    starting_duration_s: 600,
    optimized_duration_s: 420,
    starting_distance_m: 1200,
    optimized_distance_m: 800,
  },
};

function plannerComponent() {
  const component = Object.create(TripComponent.prototype) as any;
  component.trip = signal(trip);
  component.selectedDay = signal(null);
  component.plannerPreview = signal(null);
  component.plannerPreviewDayId = signal(null);
  component.plannerError = signal(null);
  component.isPlannerPreviewLoading = signal(false);
  component.isPlannerApplyLoading = signal(false);
  component.isPlannerReorderLoading = signal(false);
  component.tripPlanningPreview = signal(null);
  component.tripPlanningError = signal(null);
  component.isTripPlanningPreviewLoading = signal(false);
  component.isTripPlanningApplyLoading = signal(false);
  component.plannerProfile = 'car';
  component.utilsService = { toast: jasmine.createSpy('toast') };
  component.replaceSelectedDay = jasmine.createSpy('replaceSelectedDay');
  component.replaceSelectedDayOrder = jasmine.createSpy('replaceSelectedDayOrder');
  return component;
}

describe('TripComponent planner flow', () => {
  it('keeps the persisted day display order unchanged while previewing diagnostics', () => {
    const component = plannerComponent();
    component.dayPlanner = { preview: jasmine.createSpy().and.returnValue(of(preview)) };

    component.previewOptimizeDay(day);

    expect(component.dayPlanner.preview).toHaveBeenCalledWith(11, 7, { profile: 'car' });
    expect(component.plannerPreview()).toEqual(preview);
    expect(component.plannerPreviewFor(day).diagnostics).toEqual(preview.diagnostics);
    expect(component.trip().days[0].items.map((item: any) => item.id)).toEqual([1, 2]);
  });

  it('surfaces preview errors without exposing an Apply action state', () => {
    const component = plannerComponent();
    component.dayPlanner = {
      preview: jasmine.createSpy().and.returnValue(throwError(() => ({ error: { detail: 'No matrix' } }))),
    };

    component.previewOptimizeDay(day);

    expect(component.plannerError()).toBe('No matrix');
    expect(component.plannerPreview()).toBeNull();
  });

  it('applies only the backend-returned optimized order after an explicit preview', () => {
    const component = plannerComponent();
    component.plannerPreview.set(preview);
    component.plannerPreviewDayId.set(day.id);
    component.dayPlanner = { apply: jasmine.createSpy().and.returnValue(of({ ...preview, applied: true })) };

    component.applyOptimizeDay(day);

    expect(component.dayPlanner.apply).toHaveBeenCalledWith(11, 7, {
      profile: 'car',
      starting_item_ids: [1, 2],
    });
    expect(component.replaceSelectedDayOrder).toHaveBeenCalledWith(day, [2, 1]);
    expect(component.plannerPreview()).toBeNull();
  });

  it('does not apply a preview when routing diagnostics report no complete matrix', () => {
    const component = plannerComponent();
    component.plannerPreview.set({ ...preview, cost_comparison: null });
    component.plannerPreviewDayId.set(day.id);
    component.dayPlanner = { apply: jasmine.createSpy('apply') };

    component.applyOptimizeDay(day);

    expect(component.dayPlanner.apply).not.toHaveBeenCalled();
  });

  it('persists a keyboard-equivalent manual move for this day only', () => {
    const component = plannerComponent();
    const reorderedDay = { ...day, items: [{ ...day.items[1], sequence: 0 }, { ...day.items[0], sequence: 1 }] };
    component.dayPlanner = { reorder: jasmine.createSpy().and.returnValue(of(reorderedDay)) };

    component.movePlannerItem(day, 2, -1);

    expect(component.dayPlanner.reorder).toHaveBeenCalledWith(11, 7, { item_ids: [2, 1] });
    expect(component.replaceSelectedDay).toHaveBeenCalledWith(reorderedDay);
    expect(day.items.map((item) => item.time)).toEqual(['09:00', '10:00']);
  });

  it('supports Alt + Arrow keys as an accessible manual reorder operation', () => {
    const component = plannerComponent();
    component.movePlannerItem = jasmine.createSpy('movePlannerItem');
    const event = new KeyboardEvent('keydown', { altKey: true, key: 'ArrowUp', cancelable: true });

    component.onPlannerItemKeydown(event, day, 2);

    expect(event.defaultPrevented).toBeTrue();
    expect(component.movePlannerItem).toHaveBeenCalledWith(day, 2, -1);
  });

  it('rerenders only the selected day route in planner order after backend reconciliation', () => {
    const component = plannerComponent();
    component.dayRouting = jasmine.createSpy('dayRouting');
    component.replaceSelectedDay = (TripComponent.prototype as any).replaceSelectedDay.bind(component);
    const updatedDay = { ...day, items: [{ ...day.items[1], sequence: 0 }, { ...day.items[0], sequence: 1 }] };

    component.replaceSelectedDay(updatedDay);

    expect(component.trip().days).toEqual([updatedDay]);
    expect(component.dayRouting).toHaveBeenCalledWith(updatedDay, true);
  });

  it('keeps a whole-trip preview local until explicit Apply and lets the user cancel it', () => {
    const component = plannerComponent();
    component.tripPlanner = { preview: jasmine.createSpy().and.returnValue(of(tripPlanPreview)) };

    component.previewTripPlan();

    expect(component.tripPlanner.preview).toHaveBeenCalledWith(11);
    expect(component.tripPlanningPreview()).toEqual(tripPlanPreview);
    expect(component.trip().days).toEqual([day]);

    component.cancelTripPlanPreview();

    expect(component.tripPlanningPreview()).toBeNull();
  });

  it('sends the whole-trip stale snapshot only after explicit Apply', () => {
    const component = plannerComponent();
    component.tripPlanningPreview.set(tripPlanPreview);
    component.tripPlanner = {
      apply: jasmine.createSpy().and.returnValue(of({ ...tripPlanPreview, applied: true, applied_day_ids: [7] })),
    };
    component.apiService = { getTrip: jasmine.createSpy().and.returnValue(of(trip)) };
    component.dayRouting = jasmine.createSpy('dayRouting');

    component.applyTripPlan();

    expect(component.tripPlanner.apply).toHaveBeenCalledWith(11, {
      starting_assignments: tripPlanPreview.starting_assignments,
      snapshot_token: 'preview-token',
    });
    expect(component.apiService.getTrip).toHaveBeenCalledWith(11);
    expect(component.tripPlanningPreview()).toBeNull();
  });
});
