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
  diagnostics: [{ kind: 'coordinateless_item', item_ids: [], message: 'A diagnostic' }],
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
});
