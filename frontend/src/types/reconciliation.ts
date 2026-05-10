export interface ReconciliationLedgerEntry {
  ledger_id: string
  created_at: string | null
  root: string | null
  operation_type: string | null
  old_path: string | null
  new_path: string | null
  affected_tables: string | null
  before_values_json: string | null
  after_values_json: string | null
  status: string | null
  error: string | null
}

export interface ReconciliationPlanValidationRecord {
  action: Record<string, unknown> | unknown
  action_type: string
  status: 'valid' | 'invalid' | 'skipped'
  reason: string | null
  issues: string[]
  warnings: string[]
}

export interface ReconciliationPlanValidationResult {
  generated_at: string
  plan_path: string
  root: string
  total_actions: number
  valid_actions: number
  invalid_actions: number
  skipped_actions: number
  reasons: Record<string, number>
  validation_records: ReconciliationPlanValidationRecord[]
}

export interface ReconciliationPlanValidateRequest {
  plan_path?: string | null
  latest?: boolean
}
