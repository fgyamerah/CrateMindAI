import { apiFetch } from './client'
import type {
  ReconciliationLedgerEntry,
  ReconciliationPlanValidateRequest,
  ReconciliationPlanValidationResult,
} from '../types/reconciliation'

export function fetchReconciliationLedger(limit = 50, offset = 0): Promise<ReconciliationLedgerEntry[]> {
  return apiFetch.get<ReconciliationLedgerEntry[]>(`/reconciliation/ledger?limit=${limit}&offset=${offset}`)
}

export function fetchReconciliationLedgerEntry(ledgerId: string): Promise<ReconciliationLedgerEntry> {
  return apiFetch.get<ReconciliationLedgerEntry>(`/reconciliation/ledger/${encodeURIComponent(ledgerId)}`)
}

export function validateReconciliationPlan(
  req: ReconciliationPlanValidateRequest,
): Promise<ReconciliationPlanValidationResult> {
  return apiFetch.post<ReconciliationPlanValidationResult>('/reconciliation/validate-plan', req)
}
